"""
Tests automáticos del sistema RTU (fase 5, T5.8).
Correr desde la raíz del repo:  python -m pytest -q
Usan un histórico simulado CHICO generado al vuelo (no hace falta R2 ni
los Excel grandes).
"""
import importlib
import io
import random
from itertools import permutations

import numpy as np
import pandas as pd
import pytest

import generar_datos_rtu as gen
from esquema_rtu import EsquemaInvalido, leer_y_validar, validar
from explicacion_rtu import IndiceCasos
from markov_rtu import (ResultadoMarkov, analizar_farmaco, enumerar_estados,
                        orden_optimo, valor_secuencia)
from supuestos_protocolo import SupuestosProtocolo

N_PACIENTES_TEST = 150


@pytest.fixture(scope="session")
def historico():
    h, _ = gen.generar(n_pacientes=N_PACIENTES_TEST, seed=1)
    return h


@pytest.fixture(scope="session")
def path_historico(historico, tmp_path_factory):
    p = tmp_path_factory.mktemp("datos") / "historico_rtu_test.xlsx"
    historico.to_excel(p, index=False)
    return str(p)


# --------------------------------------------------------------- Markov

def test_estados_por_defecto():
    assert len(enumerar_estados()) == 14


def test_casos_limite_markov():
    sup = SupuestosProtocolo(prob_abandono_por_visita=0.0)
    seco = analizar_farmaco(lambda e: 0.0, sup)
    activo = analizar_farmaco(lambda e: 1.0, sup)
    assert np.isclose(seco.visitas_esperadas, 12) and np.isclose(seco.inyecciones_esperadas, 11)
    assert np.isclose(seco.prob_absorcion["estable"], 1.0)
    assert np.isclose(activo.visitas_esperadas, 6) and np.isclose(activo.inyecciones_esperadas, 5)
    assert np.isclose(activo.prob_absorcion["switch"], 1.0)


def test_probabilidades_de_absorcion_suman_uno():
    r = analizar_farmaco(lambda e: 0.4)
    assert np.isclose(sum(r.prob_absorcion.values()), 1.0)


def test_lema_de_intercambio_coincide_con_busqueda_exhaustiva():
    rng = random.Random(0)
    for _ in range(200):
        res = {f: ResultadoMarkov(0, rng.uniform(1, 30), 0,
                                  {"switch": rng.uniform(0, 0.9), "estable": 0.1, "abandono": 0.0}, {})
               for f in "ABCD"}
        cerrado = valor_secuencia(orden_optimo(res, "inyecciones"), res)["inyecciones_esperadas"]
        exhaustivo = min(valor_secuencia(list(o), res)["inyecciones_esperadas"] for o in permutations(res))
        assert np.isclose(cerrado, exhaustivo)


# ------------------------------------------------------------ Validador

def test_historico_generado_es_valido(historico):
    assert validar(historico) == []


def test_columna_faltante(historico):
    errores = validar(historico.drop(columns=["activo"]))
    assert errores and "activo" in errores[0]


def test_valores_invalidos_se_reportan_todos(historico):
    malo = historico.copy()
    malo.loc[0, "ojo"] = "XX"
    malo.loc[1, "edad"] = 200
    malo.loc[2, "activo"] = 7
    errores = validar(malo)
    assert any("'ojo'" in e for e in errores)
    assert any("'edad'" in e for e in errores)
    assert any("'activo'" in e for e in errores)


def test_emd_sin_diabetes_es_invalido(historico):
    malo = historico.copy()
    malo.loc[malo["diagnostico"] == "EMD", "diabetes"] = 0
    assert any("EMD" in e for e in validar(malo))


def test_archivo_que_no_es_excel():
    with pytest.raises(EsquemaInvalido):
        leer_y_validar(io.BytesIO(b"esto no es un excel"))


# ---------------------------------------------------- Casos similares

def test_vecinos_deterministicos_y_anonimos(historico):
    indice = IndiceCasos(historico)
    caso = {"diagnostico": "DMRE", "tipo_mnv": "MNV1", "edad": 75, "diabetes": 0}
    a = indice.explicar(caso, ["FarmacoA", "FarmacoB"], 1, 5, {})
    b = indice.explicar(caso, ["FarmacoA", "FarmacoB"], 1, 5, {})
    assert a == b
    for c in a["casos_similares"]:
        assert c["id"].startswith("caso-")
        assert set(c) == {"id", "distancia", "subtipo", "edad", "comorbilidades", "trayectoria",
                          "inyecciones_totales", "semanas_seguimiento", "desenlace_final"}
    distancias = [c["distancia"] for c in a["casos_similares"]]
    assert distancias == sorted(distancias)
    pacientes = [c["id"].rsplit("-", 1)[0] for c in a["casos_similares"]]
    assert len(pacientes) == len(set(pacientes)), "un solo ojo por paciente en el listado"


# ------------------------------------------------------------------ API

@pytest.fixture(scope="module")
def cliente(path_historico, monkeypatch_module):
    from fastapi.testclient import TestClient
    monkeypatch_module.setenv("RTU_HISTORICO_LOCAL", path_historico)
    monkeypatch_module.setenv("ADMIN_API_KEY", "clave-de-test")
    import api
    importlib.reload(api)
    return TestClient(api.app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


CASO = {"diagnostico": "DMRE", "tipo_mnv": "MNV2", "edad": 70, "diabetes": 0,
        "hipertension": 1, "acv_iam_reciente": 0, "tabaquismo": 0, "n_casos_similares": 3}


def test_health(cliente):
    j = cliente.get("/health").json()
    assert j["status"] == "ok" and j["rtu_historico_cargado"] is True


def test_sugerir_plan_ok(cliente):
    r = cliente.post("/rtu/sugerir-plan", json=CASO)
    assert r.status_code == 200
    j = r.json()
    assert sorted(j["orden_sugerido"]) == sorted(gen.FARMACOS)
    assert j["objetivo"] == "estable" and j["linea"] == 1
    assert len(j["casos_similares"]) == 3
    assert "La decisión final es del médico tratante." in j["advertencias"]
    for f in j["por_farmaco"]:
        total = f["prob_estable"] + f["prob_switch"] + f["prob_abandono"]
        assert abs(total - 1) < 1e-3 and 0 <= f["prob_estable"] <= 1
    assert [f["farmaco"] for f in j["por_farmaco"]] == j["orden_sugerido"]


def test_sugerir_plan_es_deterministico(cliente):
    a = cliente.post("/rtu/sugerir-plan", json=CASO).json()
    b = cliente.post("/rtu/sugerir-plan", json=CASO).json()
    a.pop("version_modelo"); b.pop("version_modelo")
    assert a == b


def test_ya_probados_excluye_y_sube_linea(cliente):
    j = cliente.post("/rtu/sugerir-plan", json={**CASO, "farmacos_ya_probados": ["FarmacoA"]}).json()
    assert "FarmacoA" not in j["orden_sugerido"] and j["linea"] == 2


def test_comorbilidades_faltantes_se_aceptan(cliente):
    r = cliente.post("/rtu/sugerir-plan", json={"diagnostico": "ORVR", "edad": 66})
    assert r.status_code == 200


def test_camino_A_desactivado(cliente):
    # ADR-16: solo se admite el grafo probabilístico
    assert cliente.post("/rtu/sugerir-plan", json={**CASO, "metodo": "beta"}).status_code == 422


@pytest.mark.parametrize("cuerpo", [
    {**CASO, "diagnostico": "EMD"},                 # tipo_mnv con un diagnóstico que no es DMRE
    {"diagnostico": "EMD", "edad": 60, "diabetes": 0},
    {**CASO, "edad": -1},
    {**CASO, "objetivo": "otro"},
    {"edad": 60},
])
def test_entradas_invalidas_422(cliente, cuerpo):
    assert cliente.post("/rtu/sugerir-plan", json=cuerpo).status_code == 422


def test_farmaco_desconocido_400(cliente):
    assert cliente.post("/rtu/sugerir-plan", json={**CASO, "farmacos_ya_probados": ["Inventado"]}).status_code == 400


def test_todos_probados_400(cliente):
    assert cliente.post("/rtu/sugerir-plan", json={**CASO, "farmacos_ya_probados": gen.FARMACOS}).status_code == 400


def test_admin_sin_key_401(cliente):
    r = cliente.post("/admin/rtu/actualizar-historico", files={"archivo": ("x.xlsx", b"x")})
    assert r.status_code == 401


def test_admin_archivo_invalido_400(cliente):
    r = cliente.post("/admin/rtu/actualizar-historico", headers={"X-API-Key": "clave-de-test"},
                     files={"archivo": ("x.xlsx", b"no es excel")})
    assert r.status_code == 400 and "errores" in r.json()["detail"]


# ------------------------------------------- Mejoras de arranque (T5.9)

def test_csv_equivale_al_excel(historico, path_historico, tmp_path):
    p_csv = tmp_path / "historico.csv"
    historico.to_csv(p_csv, index=False)
    desde_csv = leer_y_validar(str(p_csv), "csv")
    desde_xlsx = leer_y_validar(path_historico, "xlsx")
    assert len(desde_csv) == len(desde_xlsx)
    assert list(desde_csv.columns) == list(desde_xlsx.columns)
    assert (desde_csv["activo"].values == desde_xlsx["activo"].values).all()


def test_misma_recomendacion_desde_csv_y_excel(historico, path_historico, tmp_path):
    from servicio_rtu import ServicioRTU, cargador_archivo
    p_csv = tmp_path / "historico.csv"
    historico.to_csv(p_csv, index=False)
    caso = {k: v for k, v in CASO.items() if k != "n_casos_similares"}
    a = ServicioRTU(cargador_archivo(path_historico)).sugerir_plan(caso)
    b = ServicioRTU(cargador_archivo(str(p_csv))).sugerir_plan(caso)
    for r in (a, b):
        r.pop("version_modelo")
    assert a == b


def test_arranque_no_entrena(path_historico, monkeypatch_module):
    # Regresión del deploy fallido por "port scan timeout": arrancar el
    # servicio NO debe disparar ningún entrenamiento.
    import api
    from fastapi.testclient import TestClient
    if api.servicio_rtu._hilo is not None:  # hilo lanzado por un /health de otro test
        api.servicio_rtu._hilo.join(timeout=300)
    api.servicio_rtu._hilo = None
    api.servicio_rtu.invalidar()
    with TestClient(api.app):  # el "with" ejecuta los eventos de arranque
        assert api.servicio_rtu._hilo is None or not api.servicio_rtu._hilo.is_alive()
        assert api.servicio_rtu.estado()["rtu_modelo_entrenado"] is False


def test_health_dispara_precalentamiento(cliente):
    import time
    import api
    api.servicio_rtu.invalidar()
    primera = cliente.get("/health").json()
    assert primera["rtu_modelo_entrenado"] is False  # responde en el acto
    for _ in range(120):
        estado = cliente.get("/health").json()  # no lanza entrenamientos duplicados
        if estado["rtu_modelo_entrenado"]:
            break
        time.sleep(0.5)
    assert estado["rtu_modelo_entrenado"] is True
    assert estado["rtu_ultimo_error"] is None


# ------------------------------------ Grafo con fórmulas clásicas (ADR-16)

def test_grafo_igual_a_pgmpy(historico):
    pytest.importorskip("pgmpy")
    from referencia_pgmpy import EstimadorRedBayesianaPgmpy
    from estimacion_rtu import EstimadorRedBayesiana, TIEMPOS
    for estructura in ("factorizada", "completa"):
        nuestro = EstimadorRedBayesiana(historico, estructura)
        ref = EstimadorRedBayesianaPgmpy(historico, estructura)
        casos = [CASO, {"diagnostico": "ORVR", "edad": 66}, {"diagnostico": "EMD", "edad": 58, "diabetes": 1,
                 "hipertension": 1, "acv_iam_reciente": 0, "tabaquismo": 1}]
        for c in casos:
            for f in gen.FARMACOS:
                for t in TIEMPOS:
                    for linea in (1, 2):
                        assert abs(nuestro.p(c, f, t, linea) - ref.p(c, f, t, linea)) < 1e-12


def test_grafo_no_usa_librerias_de_aprendizaje():
    """El sistema no importa librerías de aprendizaje automático (ADR-16)."""
    import ast, inspect
    import compras_rtu, estimacion_rtu, explicacion_rtu, markov_rtu, servicio_rtu
    prohibidas = {"pgmpy", "sklearn", "torch", "tensorflow", "statsmodels", "xgboost", "lightgbm"}
    for modulo in (estimacion_rtu, compras_rtu, explicacion_rtu, markov_rtu, servicio_rtu):
        for nodo in ast.walk(ast.parse(inspect.getsource(modulo))):
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                nombres = [nodo.module or ""]
            else:
                continue
            for n in nombres:
                assert n.split(".")[0] not in prohibidas, (modulo.__name__, n)


# ------------------------------------------------- Estimación de compra

def test_estado_desde_etiqueta_ida_y_vuelta():
    from compras_rtu import estado_desde_etiqueta
    for e in enumerar_estados():
        assert estado_desde_etiqueta(e.etiqueta(), e.intervalo_semanas) == e


def test_esperanza_y_varianza_exactas_igual_a_montecarlo():
    """La programación dinámica debe coincidir con simular la misma dinámica."""
    from compras_rtu import CalculadoraDemanda, UsoHistorico
    from supuestos_protocolo import EstadoCiclo, SUPUESTOS_DEFAULT as S, transicion
    from estimacion_rtu import tiempo_de_estado

    class EstFijo:  # p_activo conocida, distinta por fármaco y momento
        base = {"FarmacoA": 0.55, "FarmacoB": 0.30, "FarmacoC": 0.40}
        def p(self, caso, f, t, linea):
            return min(0.95, self.base[f] + (0.15 if t.startswith("C") else 0.0) + 0.05 * (linea - 1))

    uso = UsoHistorico(gen.FARMACOS, {f: 1 for f in gen.FARMACOS},
                       {"FarmacoA": 0.6, "FarmacoB": 0.3, "FarmacoC": 0.1},
                       {"FarmacoA": 1.0, "FarmacoB": 3.0, "FarmacoC": 2.0})
    calc = CalculadoraDemanda(EstFijo(), uso)
    caso = {"diagnostico": "DMRE", "tipo_mnv": "MNV1", "edad": 70}
    H = 60
    E, E2 = calc.momentos(caso, EstadoCiclo.inicio(), "FarmacoA", frozenset({"FarmacoA"}), H)

    rng = np.random.default_rng(123)
    est, n = EstFijo(), 40_000
    X = np.zeros((n, 3))
    for i in range(n):
        e, f, prob, w = EstadoCiclo.inicio(), "FarmacoA", {"FarmacoA"}, H
        while True:
            d = transicion(e, bool(rng.random() < est.p(caso, f, tiempo_de_estado(e), len(prob))), S)
            if d.inyecta:
                X[i, gen.FARMACOS.index(f)] += 1
            if d.accion == "estable":
                break
            if d.accion == "switch":
                rest = [g for g in gen.FARMACOS if g not in prob]
                if not rest:
                    break
                pw = np.array([uso.peso_switch[g] for g in rest])
                f = str(rng.choice(rest, p=pw / pw.sum()))
                prob.add(f)
                e = EstadoCiclo.inicio()
                continue  # la carga del nuevo fármaco es en la misma visita
            if d.intervalo_hasta_proxima > w or rng.random() < S.prob_abandono_por_visita:
                break
            w -= d.intervalo_hasta_proxima
            e = d.siguiente
    for g in range(3):
        m, v = X[:, g].mean(), X[:, g].var()
        assert abs(E[g] - m) < 4 * np.sqrt(v / n) + 1e-9, (g, E[g], m)
        assert abs((E2[g] - E[g] ** 2) - v) / max(v, 1e-9) < 0.05, (g, E2[g] - E[g] ** 2, v)


def test_estimacion_compra_endpoint(cliente):
    r = cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26})
    assert r.status_code == 200
    j = r.json()
    assert set(j["por_farmaco"]) == set(gen.FARMACOS)
    for f, v in j["por_farmaco"].items():
        assert v["compra_sugerida"] >= v["demanda_esperada"] >= 0
        assert v["desvio_estandar"] >= 0
    assert "factor" in j["calibracion"] and "uso_historico" in j
    # la segunda llamada sale de la caché: mismo resultado
    assert cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26}).json() == j


def test_estimacion_compra_mas_nivel_mas_compra(cliente):
    a = cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26, "nivel_servicio": 0.8}).json()
    b = cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26, "nivel_servicio": 0.99}).json()
    for f in gen.FARMACOS:
        assert b["por_farmaco"][f]["compra_sugerida"] >= a["por_farmaco"][f]["compra_sugerida"]


def test_pacientes_nuevos_suman_demanda(cliente):
    a = cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26}).json()
    b = cliente.post("/rtu/estimacion-compra", json={"horizonte_semanas": 26, "nuevos_ojos_por_semana": 2}).json()
    for f in gen.FARMACOS:
        assert b["por_farmaco"][f]["demanda_esperada"] >= a["por_farmaco"][f]["demanda_esperada"]


@pytest.mark.parametrize("cuerpo", [{"horizonte_semanas": 200}, {"nivel_servicio": 1.5},
                                    {"nuevos_ojos_por_semana": -1}])
def test_estimacion_compra_422(cliente, cuerpo):
    assert cliente.post("/rtu/estimacion-compra", json=cuerpo).status_code == 422


def test_info(cliente):
    import time
    for _ in range(240):
        j = cliente.get("/rtu/info").json()
        if j["listo"]:
            break
        time.sleep(0.5)
    assert j["listo"] and sorted(j["farmacos"]) == sorted(gen.FARMACOS)
    assert j["historico"]["pacientes"] == N_PACIENTES_TEST


def test_api_solo_expone_rtu(cliente):
    """ADR-19: los endpoints legacy de vacunas se retiraron."""
    import api
    rutas = {r.path for r in api.app.routes if hasattr(r, "methods")}
    for legacy in ("/calcular-orden", "/calcular-orden-bayesiano", "/calcular-orden/reporte",
                   "/admin/actualizar-historico"):
        assert legacy not in rutas
    assert "excel_cargado" not in cliente.get("/health").json()


# ------------------------------------------ Fase 8: personalización por ojo

def test_posterior_sin_historia_es_el_prior():
    import personalizacion_rtu as P
    assert np.allclose(P.posterior_d(np.array([]), np.array([]), 0.6), P.prior_d(0.6))


def test_tau_cero_no_cambia_la_probabilidad():
    import personalizacion_rtu as P
    w = P.posterior_d(np.array([0.3, 0.3]), np.array([1, 1]), 0.0)
    assert np.allclose(P.p_personalizada(np.array([0.3, 0.7]), w), [0.3, 0.7])


def test_historia_activa_sube_la_probabilidad():
    import personalizacion_rtu as P
    pg = np.full(8, 0.4)
    activo = P.p_personalizada(0.4, P.posterior_d(pg, np.ones(8), 0.6))[0]
    seco = P.p_personalizada(0.4, P.posterior_d(pg, np.zeros(8), 0.6))[0]
    assert seco < 0.4 < activo


def test_estimar_tau_recupera_el_valor_real():
    """Ojos simulados con d ~ Normal(0, 0.7): tau estimado cerca de 0.7."""
    import personalizacion_rtu as P
    rng = np.random.default_rng(3)
    filas = []
    for o in range(3000):
        d = rng.normal(0, 0.7)
        pg = rng.uniform(0.2, 0.6, size=15)
        y = rng.random(15) < 1 / (1 + np.exp(-(np.log(pg / (1 - pg)) + d)))
        filas += [{"paciente_id": o, "ojo": "OD", "p_grafo": a, "activo": int(b)} for a, b in zip(pg, y)]
    assert abs(P.estimar_tau(pd.DataFrame(filas)).tau - 0.7) <= 0.1


def test_resumen_en_puntos_conserva_la_media():
    import personalizacion_rtu as P
    w = P.posterior_d(np.full(6, 0.35), np.array([1, 1, 0, 1, 1, 1]), 0.5)
    puntos = P.resumir_en_puntos(w, 5)
    assert abs(sum(p for _, p in puntos) - 1) < 1e-12
    assert abs(sum(d * p for d, p in puntos) - float((w * P.GRILLA_D).sum())) < 1e-9
