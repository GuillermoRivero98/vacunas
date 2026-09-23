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


def test_metodo_beta(cliente):
    j = cliente.post("/rtu/sugerir-plan", json={**CASO, "metodo": "beta"}).json()
    assert "camino_A_q6_8" in j["base_de_calculo"]["FarmacoA"]


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


def test_precalentamiento_al_arrancar(cliente):
    import time
    import api
    from fastapi.testclient import TestClient
    api.servicio_rtu.invalidar()
    with TestClient(api.app) as c:  # el "with" dispara el evento de arranque
        for _ in range(120):
            estado = c.get("/health").json()
            if estado["rtu_modelo_entrenado"]:
                break
            time.sleep(0.5)
        assert estado["rtu_modelo_entrenado"] is True
        assert estado["rtu_ultimo_error"] is None
