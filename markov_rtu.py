"""
markov_rtu.py  --  Fase 2

Cadena de Markov absorbente del protocolo treat-and-extend (T&E) para
UN ojo con UN fármaco, y orden óptimo de fármacos ante switch.

Todo determinístico (numpy). Usa EXACTAMENTE la misma regla de
transición que el generador (supuestos_protocolo.transicion), así que
lo que se modela es lo mismo que se simula.

ESTADOS
  Transitorios: los alcanzables desde C1 aplicando la regla del
    protocolo con activo=True/False (se enumeran solos por BFS):
    C1..C3 (carga), M4..M16 (mantenimiento por intervalo) y variantes
    con contadores (M4_a1, M16_s2, ...) que hacen falta para que el
    proceso sea markoviano.
  Absorbentes: "switch", "estable", "abandono".

DINÁMICA de un estado transitorio s (una visita):
  - con prob p_activo(s) la enfermedad se evalúa activa, si no seca;
  - la regla del protocolo da la acción: si es absorbente (switch /
    estable) se absorbe en la visita;
  - si no, se inyecta y se programa la próxima visita; antes de llegar
    a ella el paciente abandona con prob `prob_abandono_por_visita`.

ENTRADA CLAVE: p_activo(estado) -> float. Acá NO se estima nada: el
motor recibe esa función ya armada (la fase 3 la va a estimar con el
Camino A / B condicionada al paciente). Para probar el motor se
incluye un estimador empírico simple (estimar_p_activo_empirico).

SALIDAS (matriz fundamental N = (I - Q)^-1, arrancando en C1):
  - visitas, inyecciones y semanas esperadas hasta la absorción
  - probabilidad de terminar en estable / switch / abandono

ORDEN DE FÁRMACOS (generalización del lema de intercambio original):
  Si cada fármaco i tiene costo esperado c_i (inyecciones) y prob s_i
  de terminar en switch (única vía por la que se pasa al siguiente),
      E[iny totales | orden] = c_1 + s_1 c_2 + s_1 s_2 c_3 + ...
  y el orden óptimo es c_i / (1 - s_i) creciente (intercambio de
  adyacentes: i antes que j sii c_i (1 - s_j) <= c_j (1 - s_i)).
  El modelo viejo de vacunas es el caso particular c_i = 1,
  s_i = 1 - p_i  ->  ordenar por p_i decreciente.
  SUPUESTO: independencia entre fármacos (s_i no depende de haber
  fallado antes con otro). Los datos simulados lo violan a propósito
  (respondedor latente): se evalúa en la fase 4.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Callable

import numpy as np
import pandas as pd

from supuestos_protocolo import (
    ACCIONES_ABSORBENTES,
    SUPUESTOS_DEFAULT,
    EstadoCiclo,
    SupuestosProtocolo,
    transicion,
)

ABSORBENTES = ["switch", "estable", "abandono"]
FuncionPActivo = Callable[[EstadoCiclo], float]


# ---------------------------------------------------------------------------
# Enumeración de estados
# ---------------------------------------------------------------------------

def enumerar_estados(sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> list[EstadoCiclo]:
    """Estados transitorios alcanzables desde C1, en orden estable
    (carga primero, luego mantenimiento por intervalo y contadores)."""
    inicio = EstadoCiclo.inicio()
    vistos = {inicio}
    pendientes = [inicio]
    while pendientes:
        s = pendientes.pop()
        for activo in (True, False):
            dec = transicion(s, activo, sup)
            if dec.siguiente is not None and dec.siguiente not in vistos:
                vistos.add(dec.siguiente)
                pendientes.append(dec.siguiente)
    return sorted(vistos, key=lambda e: (
        e.fase != "carga", e.dosis_carga_previas, e.intervalo_semanas,
        e.activas_consecutivas_en_min, e.secas_consecutivas_en_max))


# ---------------------------------------------------------------------------
# Construcción de la cadena
# ---------------------------------------------------------------------------

@dataclass
class CadenaTE:
    estados: list[EstadoCiclo]
    Q: np.ndarray             # transitorio -> transitorio
    R: np.ndarray             # transitorio -> absorbente (columnas = ABSORBENTES)
    r_inyeccion: np.ndarray   # P(se inyecta en la visita al estado s)
    r_semanas: np.ndarray     # semanas esperadas hasta la próxima visita desde s

    @property
    def etiquetas(self) -> list[str]:
        return [e.etiqueta() for e in self.estados]


def construir_cadena(p_activo: FuncionPActivo,
                     sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> CadenaTE:
    estados = enumerar_estados(sup)
    idx = {e: i for i, e in enumerate(estados)}
    n = len(estados)
    Q = np.zeros((n, n))
    R = np.zeros((n, len(ABSORBENTES)))
    r_iny = np.zeros(n)
    r_sem = np.zeros(n)
    pa = sup.prob_abandono_por_visita

    for i, s in enumerate(estados):
        p = float(p_activo(s))
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"p_activo({s.etiqueta()}) = {p} fuera de [0, 1]")
        for activo, prob in ((True, p), (False, 1.0 - p)):
            if prob == 0.0:
                continue
            dec = transicion(s, activo, sup)
            if dec.inyecta:
                r_iny[i] += prob
            if dec.accion in ACCIONES_ABSORBENTES:
                R[i, ABSORBENTES.index(dec.accion)] += prob
            else:
                Q[i, idx[dec.siguiente]] += prob * (1.0 - pa)
                R[i, ABSORBENTES.index("abandono")] += prob * pa
                r_sem[i] += prob * dec.intervalo_hasta_proxima

    filas = Q.sum(axis=1) + R.sum(axis=1)
    if not np.allclose(filas, 1.0):
        raise AssertionError(f"Filas de la matriz de transición no suman 1: {filas}")
    return CadenaTE(estados, Q, R, r_iny, r_sem)


# ---------------------------------------------------------------------------
# Matriz fundamental y métricas
# ---------------------------------------------------------------------------

@dataclass
class ResultadoMarkov:
    visitas_esperadas: float
    inyecciones_esperadas: float
    semanas_esperadas: float
    prob_absorcion: dict[str, float]       # desde C1
    visitas_por_estado: dict[str, float]   # fila de N para C1 (dónde pasa el tiempo)

    def a_dict(self) -> dict:
        return {
            "visitas_esperadas": round(self.visitas_esperadas, 3),
            "inyecciones_esperadas": round(self.inyecciones_esperadas, 3),
            "semanas_esperadas": round(self.semanas_esperadas, 1),
            "prob_absorcion": {k: round(v, 4) for k, v in self.prob_absorcion.items()},
            "visitas_por_estado": {k: round(v, 3) for k, v in self.visitas_por_estado.items()},
        }


def analizar(cadena: CadenaTE) -> ResultadoMarkov:
    n = len(cadena.estados)
    N = np.linalg.solve(np.eye(n) - cadena.Q, np.eye(n))  # matriz fundamental
    B = N @ cadena.R
    inicio = cadena.estados.index(EstadoCiclo.inicio())
    fila = N[inicio]
    return ResultadoMarkov(
        visitas_esperadas=float(fila.sum()),
        inyecciones_esperadas=float(fila @ cadena.r_inyeccion),
        semanas_esperadas=float(fila @ cadena.r_semanas),
        prob_absorcion={a: float(B[inicio, j]) for j, a in enumerate(ABSORBENTES)},
        visitas_por_estado=dict(zip(cadena.etiquetas, fila.tolist())),
    )


def analizar_farmaco(p_activo: FuncionPActivo,
                     sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> ResultadoMarkov:
    return analizar(construir_cadena(p_activo, sup))


# ---------------------------------------------------------------------------
# Orden de fármacos
# ---------------------------------------------------------------------------

def valor_secuencia(orden: list[str], res: dict[str, ResultadoMarkov]) -> dict[str, float]:
    """Métricas de una secuencia de fármacos (bajo independencia).
    Se pasa al siguiente fármaco solo si el actual termina en switch."""
    llegar = 1.0
    iny = estable = 0.0
    for f in orden:
        r = res[f]
        iny += llegar * r.inyecciones_esperadas
        estable += llegar * r.prob_absorcion["estable"]
        llegar *= r.prob_absorcion["switch"]
    return {"inyecciones_esperadas": iny, "prob_estable": estable, "prob_agotar_opciones": llegar}


def orden_optimo(res: dict[str, ResultadoMarkov],
                 objetivo: str = "inyecciones") -> list[str]:
    """objetivo="inyecciones": minimiza E[inyecciones totales] con la
    regla cerrada c/(1-s). objetivo="estable": maximiza P(llegar a
    estabilidad con algún fármaco) por búsqueda exhaustiva (con 3-4
    fármacos son 6-24 órdenes, trivial)."""
    if objetivo == "inyecciones":
        def clave(f):
            s = res[f].prob_absorcion["switch"]
            return res[f].inyecciones_esperadas / max(1.0 - s, 1e-12)
        return sorted(res, key=clave)
    if objetivo == "estable":
        return list(max(permutations(res), key=lambda o: valor_secuencia(list(o), res)["prob_estable"]))
    raise ValueError("objetivo debe ser 'inyecciones' o 'estable'")


# ---------------------------------------------------------------------------
# Estimador empírico simple (SOLO para probar el motor; la fase 3 lo reemplaza)
# ---------------------------------------------------------------------------

def _clave_estado(e: EstadoCiclo) -> tuple:
    return ("carga", e.dosis_carga_previas) if e.fase == "carga" else ("mant", e.intervalo_semanas)


def estimar_p_activo_empirico(historico: pd.DataFrame, farmaco: str,
                              solo_primera_linea: bool = False,
                              alpha0: float = 1.0, beta0: float = 1.0) -> FuncionPActivo:
    """P(activo | fase, intervalo) por conteo + posterior Beta, poblacional
    (sin covariables del paciente). Supone que la actividad depende del
    intervalo pero no de los contadores del estado."""
    df = historico[historico["farmaco"] == farmaco]
    if solo_primera_linea:
        df = df[df["nro_farmaco_en_secuencia"] == 1]
    carga_idx = df["estado"].str.extract(r"^C(\d+)")[0]
    claves = np.where(
        df["fase"] == "carga",
        "carga_" + (carga_idx.astype(float) - 1).astype("Int64").astype(str),
        "mant_" + df["intervalo_transcurrido_semanas"].astype(str),
    )
    tabla = df.assign(_k=claves).groupby("_k")["activo"].agg(["sum", "count"])
    p = {k: (alpha0 + r["sum"]) / (alpha0 + beta0 + r["count"]) for k, r in tabla.iterrows()}
    p_global = (alpha0 + df["activo"].sum()) / (alpha0 + beta0 + len(df))

    def f(e: EstadoCiclo) -> float:
        tipo, v = _clave_estado(e)
        return float(p.get(f"{tipo}_{v}", p_global))
    return f


# ---------------------------------------------------------------------------
# Verificación: Monte Carlo de la misma cadena vs resultado analítico
# ---------------------------------------------------------------------------

def simular_montecarlo(p_activo: FuncionPActivo, n: int = 20_000, seed: int = 7,
                       sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> dict:
    rng = np.random.default_rng(seed)
    vis = iny = sem = 0.0
    fin = dict.fromkeys(ABSORBENTES, 0)
    for _ in range(n):
        s = EstadoCiclo.inicio()
        while True:
            vis += 1
            dec = transicion(s, bool(rng.random() < p_activo(s)), sup)
            iny += dec.inyecta
            if dec.accion in ACCIONES_ABSORBENTES:
                fin[dec.accion] += 1
                break
            sem += dec.intervalo_hasta_proxima
            if rng.random() < sup.prob_abandono_por_visita:
                fin["abandono"] += 1
                break
            s = dec.siguiente
    return {"visitas": vis / n, "inyecciones": iny / n, "semanas": sem / n,
            **{f"p_{k}": v / n for k, v in fin.items()}}


if __name__ == "__main__":
    import sys
    sup = SUPUESTOS_DEFAULT
    print(f"Estados transitorios ({len(enumerar_estados(sup))}):",
          ", ".join(e.etiqueta() for e in enumerar_estados(sup)))

    # --- 1) Casos límite con solución conocida a mano (sin abandono) ---
    sin_abandono = SupuestosProtocolo(prob_abandono_por_visita=0.0)
    r0 = analizar_farmaco(lambda e: 0.0, sin_abandono)   # siempre seco
    r1 = analizar_farmaco(lambda e: 1.0, sin_abandono)   # siempre activo
    assert np.isclose(r0.visitas_esperadas, 12) and np.isclose(r0.inyecciones_esperadas, 11)
    assert np.isclose(r0.prob_absorcion["estable"], 1.0)
    assert np.isclose(r1.visitas_esperadas, 6) and np.isclose(r1.inyecciones_esperadas, 5)
    assert np.isclose(r1.prob_absorcion["switch"], 1.0)
    print("Casos límite OK (siempre seco: 12 visitas/11 iny -> estable; "
          "siempre activo: 6 visitas/5 iny -> switch)")

    # --- 2) Analítico vs Monte Carlo con un p_activo no trivial ---
    p_prueba = lambda e: 0.6 if e.fase == "carga" else min(0.9, 0.2 + 0.02 * e.intervalo_semanas)
    ra = analizar_farmaco(p_prueba, sup)
    mc = simular_montecarlo(p_prueba, n=20_000, sup=sup)
    print("\nAnalítico vs Monte Carlo (20.000 trayectorias):")
    print(f"  visitas      {ra.visitas_esperadas:7.3f}  vs {mc['visitas']:7.3f}")
    print(f"  inyecciones  {ra.inyecciones_esperadas:7.3f}  vs {mc['inyecciones']:7.3f}")
    print(f"  semanas      {ra.semanas_esperadas:7.1f}  vs {mc['semanas']:7.1f}")
    for a in ABSORBENTES:
        print(f"  P({a:8s})  {ra.prob_absorcion[a]:7.4f}  vs {mc['p_' + a]:7.4f}")

    # --- 3) Demo sobre el histórico simulado (si se pasa el path) ---
    if len(sys.argv) > 1:
        hist = pd.read_excel(sys.argv[1])
        farmacos = sorted(hist["farmaco"].unique())
        for etiqueta, primera in (("TODAS las líneas", False), ("SOLO primera línea", True)):
            res = {f: analizar_farmaco(estimar_p_activo_empirico(hist, f, primera), sup) for f in farmacos}
            print(f"\n=== Estimación empírica poblacional, {etiqueta} ===")
            for f, r in res.items():
                pa = r.prob_absorcion
                print(f"  {f}: iny={r.inyecciones_esperadas:5.2f}  sem={r.semanas_esperadas:6.1f}  "
                      f"P(estable)={pa['estable']:.3f}  P(switch)={pa['switch']:.3f}  "
                      f"P(abandono)={pa['abandono']:.3f}")
            for obj in ("inyecciones", "estable"):
                o = orden_optimo(res, obj)
                v = valor_secuencia(o, res)
                print(f"  Orden óptimo ({obj}): {' -> '.join(o)}  |  "
                      f"E[iny]={v['inyecciones_esperadas']:.2f}  P(estable)={v['prob_estable']:.3f}")
