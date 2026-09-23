"""
explicacion_rtu.py  --  Fase 5 (T5.10, T5.11)

Explica una recomendación mostrando en qué casos históricos parecidos
se apoya (RF-19), con vecinos más cercanos (k-NN) DETERMINÍSTICOS sobre
las covariables del paciente (ADR-14). No usa ningún LLM.

Unidad de comparación: el OJO (un ojo = una trayectoria de tratamiento).

Distancia [PROPUESTA, Q-08 -- los pesos están en PESOS_DISTANCIA]:
    d = subtipo + |edad_a - edad_b| / 10 + 0.5 x (comorbilidades distintas)
    subtipo: 0 si coincide, 1 si mismo diagnóstico con distinto tipo de
             MNV, 3 si distinto diagnóstico.
    Las comorbilidades que el caso no informa no suman distancia.
    Desempate: (distancia, paciente_id, ojo) -> mismo caso, mismos
    vecinos, siempre.

Salidas:
  - base_de_calculo: por fármaco, cuántos ciclos de ese fármaco (en la
    misma línea) hubo entre los K_BASE ojos más parecidos y cómo
    terminaron. Si hay pocos, se advierte.
  - casos_similares: los N ojos más parecidos con su evolución.
  - discrepancias (T5.11): si la tasa de actividad OBSERVADA en esos
    vecinos difiere mucho de la p_activo que estima el modelo, se
    advierte (el modelo generaliza sobre todo el histórico; los vecinos
    son un subconjunto: si no concuerdan, conviene mirar con cuidado).

Privacidad (RF-20): los casos se identifican con un ID anónimo del
histórico ("caso-<paciente_id>-<ojo>"), nunca con datos personales.
El histórico no debe contener nombres, documentos ni fechas reales.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from estimacion_rtu import COMORBILIDADES, linea_cat, preparar, subtipo, tiempo_de_etiqueta

PESOS_DISTANCIA = {
    "subtipo_distinto_mismo_dx": 1.0,
    "diagnostico_distinto": 3.0,
    "edad_por_decada": 1.0,
    "comorbilidad_distinta": 0.5,
}
K_BASE = 100               # vecinos para resumir desenlaces por fármaco
MIN_CICLOS_CONFIABLE = 5    # por debajo, se advierte "pocos casos"
MIN_VISITAS_DISCREPANCIA = 20
UMBRAL_DISCREPANCIA = 0.15  # diferencia absoluta de p_activo
TIEMPO_DISCREPANCIA = "q6-8"


@dataclass(frozen=True)
class _Ojo:
    paciente_id: int
    ojo: str
    diagnostico: str
    subtipo: str
    edad: int
    comorbilidades: dict
    trayectoria: tuple  # de dicts (farmaco, linea, desenlace, inyecciones, semanas)
    inyecciones_totales: int
    semanas_seguimiento: int
    desenlace_final: str


class IndiceCasos:
    def __init__(self, historico: pd.DataFrame):
        h = historico.copy()
        h["desenlace_ciclo"] = h["desenlace_ciclo"].fillna("").astype(str)
        h = h.sort_values(["paciente_id", "ojo", "visita_nro", "nro_farmaco_en_secuencia"], kind="stable")
        ojos = []
        for (pid, ojo), g in h.groupby(["paciente_id", "ojo"], sort=True):
            f0 = g.iloc[0]
            tray = []
            for linea, c in g.groupby("nro_farmaco_en_secuencia", sort=True):
                des = c["desenlace_ciclo"][c["desenlace_ciclo"] != ""]
                tray.append({
                    "farmaco": str(c["farmaco"].iloc[0]),
                    "linea": int(linea),
                    "desenlace": str(des.iloc[-1]) if len(des) else "en_curso",
                    "inyecciones": int(c["inyectado"].sum()),
                    "semanas": int(c["semana"].max() - c["semana"].min()),
                })
            ojos.append(_Ojo(
                paciente_id=int(pid), ojo=str(ojo), diagnostico=str(f0["diagnostico"]),
                subtipo=subtipo(str(f0["diagnostico"]), str(f0["tipo_mnv"])),
                edad=int(f0["edad"]),
                comorbilidades={c: int(f0[c]) for c in COMORBILIDADES},
                trayectoria=tuple(tray),
                inyecciones_totales=int(g["inyectado"].sum()),
                semanas_seguimiento=int(g["semana"].max()),
                desenlace_final=tray[-1]["desenlace"] if tray else "en_curso",
            ))
        self.ojos = ojos
        self._dx = np.array([o.diagnostico for o in ojos])
        self._sub = np.array([o.subtipo for o in ojos])
        self._edad = np.array([o.edad for o in ojos], dtype=float)
        self._com = {c: np.array([o.comorbilidades[c] for o in ojos]) for c in COMORBILIDADES}
        self._pid = np.array([o.paciente_id for o in ojos])
        self._ojo = np.array([o.ojo for o in ojos])

        # visitas preparadas (para el chequeo de discrepancias)
        d = preparar(h)
        self._visitas = d.assign(_clave=list(zip(d["paciente_id"], d["ojo"])))

    def distancias(self, caso: dict) -> np.ndarray:
        w = PESOS_DISTANCIA
        sub = subtipo(caso["diagnostico"], caso.get("tipo_mnv"))
        d = np.where(self._sub == sub, 0.0,
                     np.where(self._dx == caso["diagnostico"], w["subtipo_distinto_mismo_dx"],
                              w["diagnostico_distinto"]))
        d = d + w["edad_por_decada"] * np.abs(self._edad - caso["edad"]) / 10.0
        for c in COMORBILIDADES:
            if caso.get(c) is not None:
                d = d + w["comorbilidad_distinta"] * (self._com[c] != int(caso[c]))
        return d

    def vecinos(self, caso: dict, k: int) -> list[tuple[_Ojo, float]]:
        d = self.distancias(caso)
        orden = np.lexsort((self._ojo, self._pid, d))[:k]  # desempate determinístico
        return [(self.ojos[i], float(d[i])) for i in orden]

    # ------------------------------------------------------------------

    def explicar(self, caso: dict, farmacos: list[str], linea: int, n_casos: int,
                 p_modelo_q8: dict[str, float]) -> dict:
        base = self.vecinos(caso, K_BASE)
        lc = linea_cat(linea)
        advertencias = []

        base_de_calculo = {}
        for f in farmacos:
            ciclos = [c for o, _ in base for c in o.trayectoria
                      if c["farmaco"] == f and linea_cat(c["linea"]) == lc]
            conteo = pd.Series([c["desenlace"] for c in ciclos]).value_counts().to_dict() if ciclos else {}
            base_de_calculo[f] = {
                "ojos_similares_considerados": len(base),
                "distancia_maxima": round(base[-1][1], 3) if base else None,
                "ciclos_con_este_farmaco": len(ciclos),
                "desenlaces": {k: int(v) for k, v in sorted(conteo.items())},
                "inyecciones_medias_por_ciclo": round(float(np.mean([c["inyecciones"] for c in ciclos])), 1) if ciclos else None,
            }
            if len(ciclos) < MIN_CICLOS_CONFIABLE:
                advertencias.append(
                    f"Pocos casos parecidos tratados con {f} en esta línea ({len(ciclos)}): "
                    f"la estimación para {f} se apoya sobre todo en el resto del histórico.")

            # T5.11: actividad observada en los vecinos vs p_activo del modelo
            claves = {(o.paciente_id, o.ojo) for o, _ in base}
            v = self._visitas
            obs = v[(v["_clave"].isin(claves)) & (v["farmaco"] == f) &
                    (v["linea"] == lc) & (v["tiempo"] == TIEMPO_DISCREPANCIA)]["activo"]
            if len(obs) >= MIN_VISITAS_DISCREPANCIA and f in p_modelo_q8:
                diferencia = float(obs.mean()) - p_modelo_q8[f]
                base_de_calculo[f]["actividad_observada_en_similares_q6_8"] = round(float(obs.mean()), 3)
                base_de_calculo[f]["actividad_estimada_por_modelo_q6_8"] = round(p_modelo_q8[f], 3)
                if abs(diferencia) > UMBRAL_DISCREPANCIA:
                    advertencias.append(
                        f"Para {f}, la actividad observada en los casos parecidos "
                        f"({obs.mean():.2f}) difiere de la estimada por el modelo "
                        f"({p_modelo_q8[f]:.2f}): revisar con cuidado.")

        casos = [{
            "id": f"caso-{o.paciente_id}-{o.ojo}",
            "distancia": round(dist, 3),
            "subtipo": o.subtipo,
            "edad": o.edad,
            "comorbilidades": o.comorbilidades,
            "trayectoria": list(o.trayectoria),
            "inyecciones_totales": o.inyecciones_totales,
            "semanas_seguimiento": o.semanas_seguimiento,
            "desenlace_final": o.desenlace_final,
        } for o, dist in base[:n_casos]]

        return {"base_de_calculo": base_de_calculo, "casos_similares": casos,
                "advertencias": advertencias,
                "criterio_similitud": {"pesos": PESOS_DISTANCIA, "k_base": K_BASE,
                                       "linea_considerada": lc}}
