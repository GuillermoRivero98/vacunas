"""
personalizacion_rtu.py  --  Fase 8

Personaliza la probabilidad de actividad de un ojo con su PROPIA historia
de controles. Todo con probabilidad clásica: regla de Bayes sobre una
grilla de valores, sin aproximaciones ni aprendizaje automático.

MODELO
  Cada ojo tiene un ajuste propio d (en la escala de log-odds) respecto de
  lo que predice el grafo para su perfil:

      logit P(activo en el control i) = logit p_grafo_i + d

  d > 0: el ojo tiende a estar más activo que lo típico de su perfil.
  Antes de ver al ojo, d ~ Normal(0, tau^2), discretizada en una grilla.

  tau (cuánto varían los ojos entre sí) se estima del histórico de
  entrenamiento por máxima verosimilitud marginal (Bayes empírico):
      L(tau) = prod_ojos  sum_d  P(d | tau) * prod_controles P(y_i | d)

  Para un ojo con controles observados y_1..y_n:
      P(d | y) ∝ P(d | tau) * prod_i p_i(d)^y_i (1 - p_i(d))^(1 - y_i)
  y la probabilidad de actividad de un control futuro es la mezcla
      P(activo) = sum_d P(d | y) * sigmoide(logit p_grafo + d)

  Sin historia (paciente nuevo), P(d | y) = P(d | tau): la mezcla sobre
  el prior, que además tiene en cuenta que los ojos de un perfil no son
  todos iguales.

QUÉ PUEDE Y QUÉ NO PUEDE MEJORAR
  d resume cuán "buen respondedor" es el ojo: desplaza la actividad de
  TODOS los fármacos en la misma dirección. Mejora el pronóstico de un ojo
  en tratamiento (y la estimación de compra), pero casi no cambia qué
  fármaco conviene probar: la parte de la respuesta propia de cada
  fármaco solo se observa probándolo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from estimacion_rtu import COMORBILIDADES, preparar

GRILLA_D = np.round(np.arange(-4.0, 4.0001, 0.1), 2)       # valores posibles de d
GRILLA_TAU = np.round(np.arange(0.0, 2.0001, 0.05), 2)     # candidatos para tau


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def _sigmoide(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def prior_d(tau: float) -> np.ndarray:
    """P(d | tau) sobre la grilla (normal discretizada; tau=0 -> todo en d=0)."""
    if tau <= 0:
        w = (GRILLA_D == 0).astype(float)
    else:
        w = np.exp(-0.5 * (GRILLA_D / tau) ** 2)
    return w / w.sum()


def log_verosimilitud_por_d(p_grafo: np.ndarray, y: np.ndarray) -> np.ndarray:
    """log prod_i P(y_i | d) para cada d de la grilla."""
    if len(y) == 0:
        return np.zeros(len(GRILLA_D))
    lg = _logit(np.asarray(p_grafo, float))[:, None] + GRILLA_D[None, :]
    p = np.clip(_sigmoide(lg), 1e-12, 1 - 1e-12)
    y = np.asarray(y, float)[:, None]
    return (y * np.log(p) + (1 - y) * np.log(1 - p)).sum(axis=0)


def posterior_d(p_grafo: np.ndarray, y: np.ndarray, tau: float) -> np.ndarray:
    """P(d | historia del ojo) sobre la grilla (regla de Bayes)."""
    lw = np.log(np.maximum(prior_d(tau), 1e-300)) + log_verosimilitud_por_d(p_grafo, y)
    lw -= lw.max()
    w = np.exp(lw)
    return w / w.sum()


def p_personalizada(p_grafo: float | np.ndarray, pesos_d: np.ndarray) -> np.ndarray:
    """P(activo) de un control futuro: mezcla sobre la posterior de d."""
    lg = _logit(np.atleast_1d(np.asarray(p_grafo, float)))[:, None] + GRILLA_D[None, :]
    return (_sigmoide(lg) * pesos_d[None, :]).sum(axis=1)


def resumir_en_puntos(pesos_d: np.ndarray, k: int = 5) -> list[tuple[float, float]]:
    """Resume la distribución de d en k puntos de igual peso (el valor medio
    de d dentro de cada k-ésimo de probabilidad). Sirve para recorrer la
    cadena de Markov una vez por punto en lugar de una por valor de la grilla."""
    acumulada = np.cumsum(pesos_d)
    puntos = []
    for j in range(k):
        lo, hi = j / k, (j + 1) / k
        masa = np.clip(np.minimum(acumulada, hi) - np.maximum(acumulada - pesos_d, lo), 0, None)
        puntos.append((float((masa * GRILLA_D).sum() / masa.sum()), 1.0 / k))
    return puntos


# ---------------------------------------------------------------------------
# Probabilidad del grafo para cada control del histórico
# ---------------------------------------------------------------------------

def caso_de_fila(fila) -> dict:
    return {"diagnostico": str(fila["diagnostico"]), "tipo_mnv": str(fila["tipo_mnv"]),
            "edad": int(fila["edad"]), **{c: int(fila[c]) for c in COMORBILIDADES}}


def p_grafo_por_control(historico: pd.DataFrame, estimador) -> pd.DataFrame:
    """Agrega a cada control la p_activo del grafo (sin personalizar).
    Devuelve paciente_id, ojo, orden, activo y p_grafo, en orden de controles."""
    h = historico.sort_values(["paciente_id", "ojo", "visita_nro", "nro_farmaco_en_secuencia"],
                              kind="stable").reset_index(drop=True)
    d = preparar(h)
    cache: dict = {}
    p = np.empty(len(h))
    for i, (fila, farm, tiempo) in enumerate(zip(h.itertuples(index=False), d["farmaco"], d["tiempo"])):
        f = fila._asdict()
        clave = (f["diagnostico"], str(f["tipo_mnv"]), int(f["edad"]) // 5,
                 tuple(int(f[c]) for c in COMORBILIDADES), farm, tiempo,
                 min(int(f["nro_farmaco_en_secuencia"]), 2))
        if clave not in cache:
            cache[clave] = estimador.p(caso_de_fila(f), farm, tiempo, int(f["nro_farmaco_en_secuencia"]))
        p[i] = cache[clave]
    out = h[["paciente_id", "ojo", "activo", "semana", "farmaco", "nro_farmaco_en_secuencia"]].copy()
    out["p_grafo"] = p
    out["orden"] = out.groupby(["paciente_id", "ojo"]).cumcount()
    return out


# ---------------------------------------------------------------------------
# Estimación de tau (Bayes empírico)
# ---------------------------------------------------------------------------

@dataclass
class AjusteTau:
    tau: float
    log_verosimilitud: dict[float, float]


def estimar_tau(controles: pd.DataFrame) -> AjusteTau:
    """tau que maximiza la verosimilitud marginal de las historias completas
    de los ojos de entrenamiento."""
    lv_por_ojo = [log_verosimilitud_por_d(g["p_grafo"].to_numpy(), g["activo"].to_numpy())
                  for _, g in controles.groupby(["paciente_id", "ojo"], sort=False)]
    lv = np.vstack(lv_por_ojo)                                   # ojos x grilla
    m = lv.max(axis=1, keepdims=True)
    resultados = {}
    for tau in GRILLA_TAU:
        pr = np.maximum(prior_d(tau), 1e-300)
        resultados[float(tau)] = float((m[:, 0] + np.log((np.exp(lv - m) * pr).sum(axis=1))).sum())
    mejor = max(resultados, key=resultados.get)
    return AjusteTau(mejor, resultados)


# ---------------------------------------------------------------------------
# Evaluación 1: predicción de controles futuros del mismo ojo
# ---------------------------------------------------------------------------

def evaluar_prediccion_controles(controles_test: pd.DataFrame, tau: float,
                                 cortes=(0, 3, 6, 12, 20)) -> pd.DataFrame:
    """Para cada control de cada ojo de prueba, predice si va a estar activo
    usando SOLO los controles anteriores del mismo ojo, y compara con la
    predicción del grafo sin personalizar. Agrupa por cuántos controles
    previos había."""
    filas = []
    for _, g in controles_test.groupby(["paciente_id", "ojo"], sort=False):
        pg, y = g["p_grafo"].to_numpy(), g["activo"].to_numpy()
        for j in range(len(g)):
            w = posterior_d(pg[:j], y[:j], tau)
            filas.append((j, y[j], pg[j], float(p_personalizada(pg[j], w)[0]),
                          float(p_personalizada(pg[j], prior_d(tau))[0])))
    r = pd.DataFrame(filas, columns=["previos", "y", "p_grafo", "p_personal", "p_mezcla_prior"])
    r["tramo"] = pd.cut(r["previos"], bins=list(cortes) + [10_000], right=False,
                        labels=[f"{a}-{b - 1}" if b < 10_000 else f"{a}+" for a, b in zip(cortes, list(cortes[1:]) + [10_000])])
    tabla = r.groupby("tramo", observed=True).apply(lambda t: pd.Series({
        "controles": len(t),
        "brier_grafo": ((t.p_grafo - t.y) ** 2).mean(),
        "brier_personalizado": ((t.p_personal - t.y) ** 2).mean(),
        "actividad_real": t.y.mean(),
    }), include_groups=False)
    total = pd.DataFrame([{
        "controles": len(r),
        "brier_grafo": ((r.p_grafo - r.y) ** 2).mean(),
        "brier_personalizado": ((r.p_personal - r.y) ** 2).mean(),
        "actividad_real": r.y.mean(),
    }], index=["todos"])
    tabla = pd.concat([tabla, total])
    tabla["mejora_%"] = 100 * (tabla.brier_grafo - tabla.brier_personalizado) / tabla.brier_grafo
    return tabla
