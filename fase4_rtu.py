"""
fase4_rtu.py  --  Fase 4 (análisis offline, no se usa en producción)

Dos preguntas:

1. ¿Se sostiene el supuesto de INDEPENDENCIA entre fármacos?
   Si la respuesta a un fármaco no depende de haber fallado antes con
   otro, la tasa de actividad de ese fármaco debería ser la misma en
   primera línea que en segunda o posterior (a igual momento del
   tratamiento y subtipo diagnóstico). Se testea con Cochran-Mantel-
   Haenszel (CMH), estratificando por tiempo x subtipo, y se reporta el
   odds ratio común de Mantel-Haenszel (OR > 1: en línea 2+ la
   enfermedad está más activa -> la independencia NO se sostiene).
   Es un test que se puede correr igual con datos reales.

2. ¿Mejora la recomendación usar estimaciones de línea 2+ para las
   posiciones 2 y 3 de la secuencia?
   Para cada ojo de test se SIMULA qué hubiera pasado con cada uno de
   los órdenes posibles de fármacos, usando los parámetros latentes
   VERDADEROS de ese ojo (verdad oculta) y exactamente la misma
   dinámica que el generador. Con números aleatorios comunes (misma
   semilla por ojo y réplica para todos los órdenes) las comparaciones
   entre órdenes tienen mucho menos ruido. Después cada política se
   puntúa por el orden que eligió. El "oráculo" elige, para cada ojo,
   el mejor orden según la simulación con la verdad: es el techo. Para
   que no quede inflado, elige con una mitad de las réplicas y se
   puntúa (igual que todas las políticas) con la otra mitad.
"""
from __future__ import annotations

import sys
import time
from itertools import permutations

import numpy as np
import pandas as pd
from scipy.stats import chi2

import generar_datos_rtu as gen
from estimacion_rtu import (
    EstimadorFrecuencias,
    EstimadorRedBayesiana,
    preparar,
    recomendar,
    recomendar_por_linea,
)
from evaluar_rtu import caso_desde_fila, particionar
from supuestos_protocolo import (
    ACCION_ESTABLE,
    ACCION_SWITCH,
    SUPUESTOS_DEFAULT,
    EstadoCiclo,
    SupuestosProtocolo,
    evaluar_actividad,
    transicion,
)

N_REPLICAS = 60
SEED_SIM = 4242


# ---------------------------------------------------------------------------
# 1. Test de independencia: Cochran-Mantel-Haenszel
# ---------------------------------------------------------------------------

def test_cmh(df: pd.DataFrame, farmaco: str) -> dict:
    """Tabla 2x2 por estrato (tiempo x subtipo):
         filas = línea 2+ / línea 1, columnas = activo / seco."""
    d = df[df["farmaco"] == farmaco]
    num_or = den_or = suma_a = suma_ea = suma_var = 0.0
    n_estratos = 0
    for _, g in d.groupby(["tiempo", "subtipo"]):
        l2, l1 = g[g["linea"] == "2+"], g[g["linea"] == "1"]
        a, b = l2["activo"].sum(), (1 - l2["activo"]).sum()
        c, dd = l1["activo"].sum(), (1 - l1["activo"]).sum()
        n = a + b + c + dd
        if min(a + b, c + dd, a + c, b + dd) == 0 or n < 2:
            continue
        n_estratos += 1
        num_or += a * dd / n
        den_or += b * c / n
        suma_a += a
        suma_ea += (a + b) * (a + c) / n
        suma_var += (a + b) * (c + dd) * (a + c) * (b + dd) / (n ** 2 * (n - 1))
    if n_estratos == 0 or suma_var == 0:
        return {"farmaco": farmaco, "estratos": 0}
    estadistico = (abs(suma_a - suma_ea) - 0.5) ** 2 / suma_var
    return {
        "farmaco": farmaco,
        "tasa_activo_linea1": d[d["linea"] == "1"]["activo"].mean(),
        "tasa_activo_linea2+": d[d["linea"] == "2+"]["activo"].mean(),
        "visitas_linea2+": int((d["linea"] == "2+").sum()),
        "estratos": n_estratos,
        "OR_MH": num_or / den_or,
        "chi2_CMH": estadistico,
        "p_valor": float(chi2.sf(estadistico, 1)),
    }


# ---------------------------------------------------------------------------
# 2. Simulador de la verdad: un ojo, un orden de fármacos
# ---------------------------------------------------------------------------

def reconstruir_ojos(test: pd.DataFrame, verdad: pd.DataFrame) -> list[dict]:
    """Paciente con sus efectos latentes VERDADEROS, para simular."""
    v = verdad.set_index(["paciente_id", "ojo", "farmaco"])
    ojos = []
    for fila in test.drop_duplicates(["paciente_id", "ojo"]).itertuples():
        clave = (fila.paciente_id, fila.ojo)
        pac = gen.Paciente(
            paciente_id=fila.paciente_id, edad=int(fila.edad), diagnostico=fila.diagnostico,
            diabetes=int(fila.diabetes), hipertension=int(fila.hipertension),
            acv_iam_reciente=int(fila.acv_iam_reciente), tabaquismo=int(fila.tabaquismo),
            glaucoma=int(fila.glaucoma), cristalino=fila.cristalino, tipo_mnv=str(fila.tipo_mnv),
            u_paciente=float(v.loc[clave + (gen.FARMACOS[0],), "u_paciente"]),
            u_paciente_farmaco={f: float(v.loc[clave + (f,), "u_paciente_farmaco"]) for f in gen.FARMACOS},
        )
        ojos.append({"clave": clave, "paciente": pac,
                     "u_ojo": float(v.loc[clave + (gen.FARMACOS[0],), "u_ojo"]),
                     "caso": caso_desde_fila(pd.Series(fila._asdict()))})
    return ojos


def simular_orden(pac: gen.Paciente, u_ojo: float, orden: tuple[str, ...],
                  rng: np.random.Generator,
                  sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> tuple[int, bool]:
    """Misma dinámica que generar_datos_rtu.generar, pero con el orden de
    fármacos IMPUESTO y sin horizonte (termina por estable, abandono o
    agotar opciones). Devuelve (inyecciones, llegó_a_estable)."""
    cmt = float(np.clip(rng.normal(480, 90), 250, 850))
    av = float(np.clip(rng.uniform(0.1, 0.5), 0.02, 1.0))
    av_pot = float(min(1.0, av + rng.uniform(0.1, 0.4)))
    cmt_prev = av_prev = None
    estado, i, visita, iny = EstadoCiclo.inicio(), 0, 0, 0
    while visita < 2000:
        if visita > 0 and rng.random() < sup.prob_abandono_por_visita:
            return iny, False
        visita += 1
        f = orden[i]
        activo = bool(rng.random() < gen._sigmoid(gen.logit_actividad(pac, u_ojo, f, estado, sup)))
        irf, srf, cmt, av = gen._observables(rng, activo, cmt, av, av_pot)
        ev = evaluar_actividad(irf, srf, cmt, cmt_prev, av, av_prev, sup)
        cmt_prev, av_prev = cmt, av
        dec = transicion(estado, ev, sup)
        iny += dec.inyecta
        if dec.accion == ACCION_ESTABLE:
            return iny, True
        if dec.accion == ACCION_SWITCH:
            i += 1
            if i >= len(orden):
                return iny, False
            estado = EstadoCiclo.inicio()
            dec = transicion(estado, ev, sup)
            iny += 1
        estado = dec.siguiente
    return iny, False


def tabla_verdad_por_orden(ojos: list[dict], n_replicas: int = N_REPLICAS) -> tuple[dict, dict]:
    """Para cada ojo y cada orden: (inyecciones medias, P(estable)) con la
    verdad, en DOS mitades independientes de réplicas: la mitad "sel"
    solo la usa el oráculo para elegir, y la mitad "eval" se usa para
    puntuar a TODAS las políticas. Así el techo del oráculo no queda
    inflado por elegir el mínimo de promedios ruidosos. Números
    aleatorios comunes entre órdenes dentro de cada réplica."""
    ordenes = list(permutations(gen.FARMACOS))
    sel, ev = {}, {}
    for j, o in enumerate(ojos):
        for orden in ordenes:
            acum = {0: [0, 0, 0], 1: [0, 0, 0]}  # mitad -> [iny, estable, n]
            for r in range(n_replicas):
                rng = np.random.default_rng([SEED_SIM, j, r])
                a, b = simular_orden(o["paciente"], o["u_ojo"], orden, rng)
                m = acum[r % 2]
                m[0] += a
                m[1] += b
                m[2] += 1
            sel[(o["clave"], orden)] = (acum[0][0] / acum[0][2], acum[0][1] / acum[0][2])
            ev[(o["clave"], orden)] = (acum[1][0] / acum[1][2], acum[1][1] / acum[1][2])
    return sel, ev


def puntuar_politicas(ojos, tabla_sel, tabla_eval, elecciones: dict[str, list[tuple]]) -> pd.DataFrame:
    ordenes = list(permutations(gen.FARMACOS))
    oraculo = [min(ordenes, key=lambda od: tabla_sel[(o["clave"], od)][0]) for o in ojos]
    elecciones = {**elecciones, "techo_oraculo": oraculo}
    iny_or = np.array([tabla_eval[(o["clave"], od)][0] for o, od in zip(ojos, oraculo)])
    filas = []
    for nombre, elegidos in elecciones.items():
        iny = np.array([tabla_eval[(o["clave"], tuple(e))][0] for o, e in zip(ojos, elegidos)])
        est = np.array([tabla_eval[(o["clave"], tuple(e))][1] for o, e in zip(ojos, elegidos)])
        filas.append({
            "politica": nombre,
            "iny_verdaderas": iny.mean(),
            "P_estable_verdadera": est.mean(),
            "exceso_iny_vs_oraculo": (iny - iny_or).mean(),
            "1er_farmaco_como_oraculo": np.mean([e[0] == od[0] for e, od in zip(elegidos, oraculo)]),
        })
    return pd.DataFrame(filas).sort_values("iny_verdaderas")


if __name__ == "__main__":
    path_hist = sys.argv[1] if len(sys.argv) > 1 else "historico_rtu_SIMULADO.xlsx"
    path_verdad = sys.argv[2] if len(sys.argv) > 2 else "verdad_oculta_rtu_SIMULADO.xlsx"
    historico = pd.read_excel(path_hist)
    verdad = pd.read_excel(path_verdad)
    pd.set_option("display.width", 160)

    print("=" * 78)
    print("1) Test de independencia entre fármacos (CMH, estratos tiempo x subtipo)")
    print("=" * 78)
    d = preparar(historico)
    cmh = pd.DataFrame([test_cmh(d, f) for f in gen.FARMACOS])
    print(cmh.round(4).to_string(index=False))
    print("OR_MH > 1: en línea 2+ la enfermedad está más activa que en línea 1 "
          "a igual momento y subtipo -> la respuesta NO es independiente del "
          "fracaso previo.")

    print()
    print("=" * 78)
    print("2) Políticas de orden vs VERDAD simulada por ojo (test, arranque en línea 1)")
    print("=" * 78)
    train, test = particionar(historico)
    ojos = reconstruir_ojos(test, verdad)
    print(f"Ojos de test: {len(ojos)} | órdenes por ojo: 6 | réplicas: {N_REPLICAS}")

    t0 = time.time()
    tabla_sel, tabla_eval = tabla_verdad_por_orden(ojos)
    print(f"Simulación de la verdad: {time.time() - t0:.0f}s")

    # a = EstimadorBeta(train)   # Camino A desactivado (ADR-16)
    b = EstimadorRedBayesiana(train, "factorizada")
    pob = EstimadorFrecuencias(train)
    orden_pob = recomendar(pob, {"edad": 70, "diagnostico": "DMRE"}, gen.FARMACOS, objetivo="inyecciones").orden

    rng = np.random.default_rng(0)
    elecciones = {
        "azar": [tuple(rng.permutation(gen.FARMACOS)) for _ in ojos],
        "poblacional_fijo": [tuple(orden_pob)] * len(ojos),
        # Camino A desactivado (ADR-16):
        # "A_independiente": [tuple(recomendar(a, o["caso"], gen.FARMACOS, objetivo="inyecciones").orden) for o in ojos],
        # "A_por_linea": [tuple(recomendar_por_linea(a, o["caso"], gen.FARMACOS, objetivo="inyecciones").orden) for o in ojos],
        "B_independiente": [tuple(recomendar(b, o["caso"], gen.FARMACOS, objetivo="inyecciones").orden) for o in ojos],
        "B_por_linea": [tuple(recomendar_por_linea(b, o["caso"], gen.FARMACOS, objetivo="inyecciones").orden) for o in ojos],
    }
    print(f"Orden poblacional fijo: {' -> '.join(orden_pob)}")
    print()
    print(puntuar_politicas(ojos, tabla_sel, tabla_eval, elecciones).round(3).to_string(index=False))

    print("\nCuántos ojos cambian de orden al pasar de 'independiente' a 'por línea':")
    for cam in ("B",):  # "A" desactivado (ADR-16)
        ind, lin = elecciones[f"{cam}_independiente"], elecciones[f"{cam}_por_linea"]
        print(f"  Camino {cam}: {np.mean([x != y for x, y in zip(ind, lin)]):.1%}")
