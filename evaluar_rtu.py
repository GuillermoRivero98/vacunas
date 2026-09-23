"""
evaluar_rtu.py  --  Fase 3 (evaluación offline, no se usa en producción)

Compara los estimadores de p_activo con partición train/test POR
PACIENTE (los dos ojos de un paciente quedan del mismo lado), en tres
niveles:

  1. Predicción de visitas (lo único medible con datos reales):
     Brier y log-loss sobre la actividad evaluada en visitas de test.

  2. Elección del fármaco contra la VERDAD OCULTA (solo posible con
     datos simulados): para cada ojo de test, ¿el fármaco con menor
     p_activo estimado coincide con el de menor p_activo verdadero?
     Se reporta acierto top-1, Spearman y "arrepentimiento" (cuánto peor
     es, en p_activo verdadero, el fármaco elegido que el mejor).
     Techo de referencia ("oráculo de covariables"): el mejor resultado
     posible usando SOLO las variables observables del paciente, con los
     coeficientes verdaderos del generador. Ningún estimador basado en
     covariables puede superarlo en esperanza, porque la parte
     idiosincrática paciente x fármaco no es observable.

  3. Demo de punta a punta: recomendación vía cadena de Markov para un
     paciente de test con cada camino.

Los estimadores entrenan SOLO con train y nunca leen la verdad oculta;
la verdad se usa únicamente acá, para puntuar.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import generar_datos_rtu as gen
from estimacion_rtu import (
    EstimadorBeta,
    EstimadorRedBayesiana,
    preparar,
    recomendar,
)
from supuestos_protocolo import SUPUESTOS_DEFAULT, EstadoCiclo

TIEMPO_REFERENCIA = "q6-8"  # la verdad oculta está a intervalo 8 semanas
SEED_SPLIT = 12345


def particionar(historico: pd.DataFrame, frac_test: float = 0.3, seed: int = SEED_SPLIT):
    rng = np.random.default_rng(seed)
    pacientes = historico["paciente_id"].unique()
    rng.shuffle(pacientes)
    test = set(pacientes[: int(len(pacientes) * frac_test)])
    es_test = historico["paciente_id"].isin(test)
    return historico[~es_test].copy(), historico[es_test].copy()


def caso_desde_fila(fila: pd.Series) -> dict:
    return {k: (fila[k].item() if hasattr(fila[k], "item") else fila[k]) for k in
            ["edad", "diagnostico", "tipo_mnv", "diabetes", "hipertension",
             "acv_iam_reciente", "tabaquismo"]}


def evaluar_visitas(estimadores, test: pd.DataFrame) -> pd.DataFrame:
    d = preparar(test)
    casos = [caso_desde_fila(f) for _, f in test.iterrows()]
    lineas = test["nro_farmaco_en_secuencia"].to_numpy()
    y = d["activo"].to_numpy()
    filas = []
    for est in estimadores:
        p = np.array([est.p(c, f, t, l) for c, f, t, l in
                      zip(casos, d["farmaco"], d["tiempo"], lineas)])
        pc = np.clip(p, 1e-6, 1 - 1e-6)
        filas.append({
            "camino": est.nombre,
            "brier": np.mean((p - y) ** 2),
            "log_loss": -np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)),
            "p_media": p.mean(), "tasa_observada": y.mean(),
        })
    return pd.DataFrame(filas)


def _p_oraculo_covariables(fila: pd.Series, farmaco: str) -> float:
    """p_activo con los coeficientes VERDADEROS pero sin los efectos
    latentes (u = 0): lo máximo que se puede saber de las covariables."""
    pac = gen.Paciente(
        paciente_id=0, edad=int(fila["edad"]), diagnostico=fila["diagnostico"],
        diabetes=int(fila["diabetes"]), hipertension=int(fila["hipertension"]),
        acv_iam_reciente=int(fila["acv_iam_reciente"]), tabaquismo=int(fila["tabaquismo"]),
        glaucoma=int(fila["glaucoma"]), cristalino=fila["cristalino"], tipo_mnv=str(fila["tipo_mnv"]),
        u_paciente=0.0, u_paciente_farmaco={f: 0.0 for f in gen.FARMACOS},
    )
    e = EstadoCiclo(fase="mantenimiento", intervalo_semanas=gen.INTERVALO_REFERENCIA_VERDAD)
    return gen._sigmoid(gen.logit_actividad(pac, 0.0, farmaco, e, SUPUESTOS_DEFAULT))


def evaluar_eleccion(estimadores, test: pd.DataFrame, verdad: pd.DataFrame) -> pd.DataFrame:
    ojos = test.drop_duplicates(["paciente_id", "ojo"])
    v = verdad.set_index(["paciente_id", "ojo", "farmaco"])["p_activo_verdadera_q8"]
    farmacos = gen.FARMACOS

    p_true = np.array([[v[(r.paciente_id, r.ojo, f)] for f in farmacos] for r in ojos.itertuples()])
    mejor_true = p_true.argmin(axis=1)

    def puntuar(nombre, p_est):
        elegido = p_est.argmin(axis=1)
        idx = np.arange(len(ojos))
        rho = np.mean([spearmanr(p_est[i], p_true[i]).statistic for i in idx
                       if np.ptp(p_est[i]) > 0])
        return {
            "camino": nombre,
            "acierto_top1": np.mean(elegido == mejor_true),
            "spearman_medio_por_ojo": rho,
            "arrepentimiento_medio": np.mean(p_true[idx, elegido] - p_true[idx, mejor_true]),
        }

    filas = []
    for est in estimadores:
        p_est = np.array([[est.p(caso_desde_fila(pd.Series(r._asdict())), f, TIEMPO_REFERENCIA, 1)
                           for f in farmacos] for r in ojos.itertuples()])
        filas.append(puntuar(est.nombre, p_est))

    p_orac = np.array([[_p_oraculo_covariables(pd.Series(r._asdict()), f) for f in farmacos]
                       for r in ojos.itertuples()])
    filas.append(puntuar("techo_oraculo_covariables", p_orac))
    rng = np.random.default_rng(0)
    filas.append(puntuar("azar", rng.random(p_true.shape)))
    return pd.DataFrame(filas)


if __name__ == "__main__":
    path_hist = sys.argv[1] if len(sys.argv) > 1 else "historico_rtu_SIMULADO.xlsx"
    path_verdad = sys.argv[2] if len(sys.argv) > 2 else "verdad_oculta_rtu_SIMULADO.xlsx"
    historico = pd.read_excel(path_hist)
    verdad = pd.read_excel(path_verdad)
    train, test = particionar(historico)
    print(f"Train: {train['paciente_id'].nunique()} pacientes / {len(train)} visitas | "
          f"Test: {test['paciente_id'].nunique()} pacientes / {len(test)} visitas")

    t0 = time.time()
    estimadores = [
        EstimadorBeta(train, usar_covariables=False, estratificar_linea=False),
        EstimadorBeta(train, usar_covariables=False),
        EstimadorBeta(train, estratificar_linea=False),
        EstimadorBeta(train),
        EstimadorRedBayesiana(train, "completa"),
        EstimadorRedBayesiana(train, "factorizada"),
    ]
    print(f"Entrenamiento: {time.time() - t0:.1f}s")

    pd.set_option("display.width", 140)
    print("\n1) Predicción de actividad en visitas de test (más bajo = mejor)")
    print(evaluar_visitas(estimadores, test).round(4).to_string(index=False))

    print("\n2) Elección de fármaco por ojo vs VERDAD OCULTA (línea 1, intervalo 8 sem)")
    print(evaluar_eleccion(estimadores, test, verdad).round(4).to_string(index=False))

    print("\n3) Demo de punta a punta: recomendación vía Markov para un ojo de test")
    fila = test.drop_duplicates(["paciente_id", "ojo"]).iloc[0]
    caso = caso_desde_fila(fila)
    print(f"   Caso: {caso}")
    vt = verdad[(verdad.paciente_id == fila.paciente_id) & (verdad.ojo == fila.ojo)]
    print("   p_activo verdadero (q8):", dict(zip(vt.farmaco, vt.p_activo_verdadera_q8.round(3))))
    for est in (estimadores[3], estimadores[5]):
        rec = recomendar(est, caso, gen.FARMACOS, objetivo="inyecciones")
        print(f"   [{est.nombre}] orden: {' -> '.join(rec.orden)} | "
              f"E[iny]={rec.valor_orden['inyecciones_esperadas']:.1f} | "
              f"P(estable)={rec.valor_orden['prob_estable']:.3f}")
        for f, r in rec.por_farmaco.items():
            print(f"       {f}: iny={r.inyecciones_esperadas:5.1f}  "
                  f"P(estable)={r.prob_absorcion['estable']:.3f}  P(switch)={r.prob_absorcion['switch']:.3f}")
