"""
generar_datos_rtu.py

Genera un histórico SIMULADO de una unidad de terapia retinal (RTU):
inyecciones intravítreas anti-VEGF con protocolo treat-and-extend.
Una fila por evaluación de ciclo (visita x ojo x fármaco en curso).

Reemplaza conceptualmente a generar_datos_sinteticos.py (esquema de
"vacunas"), pero NO lo toca: es un módulo nuevo y aditivo.

Diseño clave -- VERDAD OCULTA SEPARADA:
  El generador decide internamente la probabilidad REAL de que la
  enfermedad esté activa para cada (paciente, ojo, fármaco), incluidos
  los fármacos que ese ojo nunca recibió (contrafácticos). Eso se
  exporta a un archivo APARTE (verdad_oculta_rtu_SIMULADO.xlsx) que
  los motores de estimación NO deben leer nunca: sirve solo para medir
  qué tan bien recupera cada camino la verdad. Con datos reales esto es
  imposible; es la principal razón para trabajar primero con sintéticos.

Fenómenos que se simulan A PROPÓSITO (para que el análisis los detecte):
  - Efecto "respondedor" latente por paciente, compartido entre ojos y
    entre fármacos -> las respuestas a distintos fármacos NO son
    independientes, y llegan al segundo fármaco sobre todo los malos
    respondedores (sesgo de selección en el historial de switches).
  - Componente paciente x fármaco -> el switch puede ayudar de verdad.
  - Interacción diagnóstico x fármaco (oculta).
  - Covariables con efecto real y otras SIN efecto (glaucoma,
    cristalino): el modelo no debería atribuirles señal.
  - Misclasificación: la actividad "evaluada" (regla del protocolo
    sobre observables ruidosos) no siempre coincide con la latente.

Uso exclusivo para desarrollo/demo -- NO son datos de pacientes reales.
Los fármacos se llaman FarmacoA/B/C a propósito: los efectos son
inventados y no deben leerse como afirmaciones sobre fármacos reales.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from supuestos_protocolo import (
    ACCION_ESTABLE,
    ACCION_SWITCH,
    SUPUESTOS_DEFAULT,
    EstadoCiclo,
    SupuestosProtocolo,
    evaluar_actividad,
    transicion,
)

RNG_SEED = 2026
HORIZONTE_SEMANAS = 208  # 4 años de seguimiento máximo por ojo

FARMACOS = ["FarmacoA", "FarmacoB", "FarmacoC"]

# ---------------------------------------------------------------------------
# PARÁMETROS DE LA VERDAD OCULTA (log-odds de enfermedad ACTIVA en una
# visita de mantenimiento). Positivo = más actividad = peor.
# ---------------------------------------------------------------------------
PREVALENCIA_DX = {"DMRE": 0.50, "EMD": 0.30, "OVCR": 0.10, "ORVR": 0.10}
BASE_ACTIVIDAD_DX = {"DMRE": -0.3, "EMD": -0.5, "OVCR": -0.4, "ORVR": -1.0}
EFICACIA_FARMACO = {"FarmacoA": 0.6, "FarmacoB": 1.1, "FarmacoC": 0.9}
INTERACCION_DX_FARMACO = {("EMD", "FarmacoC"): 0.5, ("DMRE", "FarmacoB"): 0.2}

COEF = {
    "intervalo": 0.12,        # por semana por encima de 8
    "edad": 0.02,             # por año por encima de 70
    "hipertension": 0.20,
    "acv_iam_reciente": 0.30,
    "tabaquismo": 0.30,
    "mnv3": 0.40,             # solo DMRE
    "mnv1": -0.20,            # solo DMRE
    "carga_por_dosis_faltante": 0.8,
    "glaucoma": 0.0,          # SIN efecto a propósito
    "pseudofaquico": 0.0,     # SIN efecto a propósito
}
SD_RESPONDEDOR_PACIENTE = 0.8
SD_OJO = 0.3
SD_PACIENTE_FARMACO = 0.5
INTERVALO_REFERENCIA_VERDAD = 8  # semanas, para exportar p_activo verdadera

# Práctica de prescripción simulada (quién recibe qué primero / al switchear)
PESOS_PRIMER_FARMACO = {"FarmacoA": 0.65, "FarmacoB": 0.25, "FarmacoC": 0.10}
PESOS_SWITCH = {"FarmacoA": 0.2, "FarmacoB": 0.5, "FarmacoC": 0.3}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def _elegir(rng, candidatos: list[str], pesos: dict[str, float]) -> str:
    w = np.array([pesos[c] for c in candidatos], dtype=float)
    return str(rng.choice(candidatos, p=w / w.sum()))


@dataclass
class Paciente:
    paciente_id: int
    edad: int
    diagnostico: str
    diabetes: int
    hipertension: int
    acv_iam_reciente: int
    tabaquismo: int
    glaucoma: int
    cristalino: str
    tipo_mnv: str
    u_paciente: float
    u_paciente_farmaco: dict[str, float]


def _generar_paciente(rng, paciente_id: int) -> Paciente:
    dx = str(rng.choice(list(PREVALENCIA_DX), p=list(PREVALENCIA_DX.values())))
    media_edad = {"DMRE": 77, "EMD": 62, "OVCR": 68, "ORVR": 66}[dx]
    edad = int(np.clip(rng.normal(media_edad, 8), 35, 98))

    diabetes = 1 if dx == "EMD" else int(rng.random() < 0.18)
    hipertension = int(rng.random() < (0.55 if dx in ("OVCR", "ORVR") else 0.40))
    acv_iam = int(rng.random() < 0.06)
    tabaquismo = int(rng.random() < 0.15)
    glaucoma = int(rng.random() < (0.20 if dx == "OVCR" else 0.08))
    cristalino = "pseudofaquico" if rng.random() < np.clip((edad - 50) / 60, 0.05, 0.7) else "faquico"
    tipo_mnv = str(rng.choice(["MNV1", "MNV2", "MNV3"], p=[0.5, 0.25, 0.25])) if dx == "DMRE" else "NA"

    return Paciente(
        paciente_id=paciente_id, edad=edad, diagnostico=dx, diabetes=diabetes,
        hipertension=hipertension, acv_iam_reciente=acv_iam, tabaquismo=tabaquismo,
        glaucoma=glaucoma, cristalino=cristalino, tipo_mnv=tipo_mnv,
        u_paciente=float(rng.normal(0, SD_RESPONDEDOR_PACIENTE)),
        u_paciente_farmaco={f: float(rng.normal(0, SD_PACIENTE_FARMACO)) for f in FARMACOS},
    )


def logit_actividad(p: Paciente, u_ojo: float, farmaco: str, estado: EstadoCiclo,
                    sup: SupuestosProtocolo) -> float:
    """Log-odds VERDADERO de enfermedad activa en esta visita."""
    intervalo = (sup.intervalo_carga_semanas if estado.fase == "carga"
                 else estado.intervalo_semanas)
    eta = (
        BASE_ACTIVIDAD_DX[p.diagnostico]
        - EFICACIA_FARMACO[farmaco]
        - INTERACCION_DX_FARMACO.get((p.diagnostico, farmaco), 0.0)
        + COEF["intervalo"] * (intervalo - 8)
        + COEF["edad"] * (p.edad - 70)
        + COEF["hipertension"] * p.hipertension
        + COEF["acv_iam_reciente"] * p.acv_iam_reciente
        + COEF["tabaquismo"] * p.tabaquismo
        + COEF["mnv3"] * (p.tipo_mnv == "MNV3")
        + COEF["mnv1"] * (p.tipo_mnv == "MNV1")
        + COEF["glaucoma"] * p.glaucoma
        + COEF["pseudofaquico"] * (p.cristalino == "pseudofaquico")
        - p.u_paciente - u_ojo - p.u_paciente_farmaco[farmaco]
    )
    if estado.fase == "carga":
        eta += COEF["carga_por_dosis_faltante"] * (sup.dosis_carga - estado.dosis_carga_previas)
    return float(eta)


def _observables(rng, activo_latente: bool, cmt_prev: float, av_prev: float,
                 av_potencial: float) -> tuple[bool, bool, float, float]:
    irf = bool(rng.random() < (0.75 if activo_latente else 0.05))
    srf = bool(rng.random() < (0.60 if activo_latente else 0.05))
    if activo_latente:
        cmt = cmt_prev + rng.normal(60, 40)
        av = av_prev - abs(rng.normal(0.08, 0.05))
    else:
        cmt = cmt_prev - 0.3 * (cmt_prev - 280) + rng.normal(0, 15)
        av = av_prev + 0.2 * (av_potencial - av_prev) + rng.normal(0, 0.03)
    return irf, srf, float(np.clip(cmt, 200, 900)), float(np.clip(av, 0.02, 1.0))


def generar(n_pacientes: int = 1500, prob_bilateral: float = 0.25,
            sup: SupuestosProtocolo = SUPUESTOS_DEFAULT,
            seed: int = RNG_SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (historico, verdad_oculta)."""
    rng = np.random.default_rng(seed)
    filas: list[dict] = []
    verdad: list[dict] = []

    for pid in range(1, n_pacientes + 1):
        p = _generar_paciente(rng, pid)
        ojos = ["OD", "OI"] if rng.random() < prob_bilateral else [str(rng.choice(["OD", "OI"]))]

        for ojo in ojos:
            u_ojo = float(rng.normal(0, SD_OJO))

            # --- verdad oculta: p_activo en mantenimiento a intervalo de
            #     referencia, para TODOS los fármacos (contrafáctico) ---
            estado_ref = EstadoCiclo(fase="mantenimiento", intervalo_semanas=INTERVALO_REFERENCIA_VERDAD)
            for f in FARMACOS:
                verdad.append({
                    "paciente_id": pid, "ojo": ojo, "farmaco": f,
                    "p_activo_verdadera_q8": round(_sigmoid(logit_actividad(p, u_ojo, f, estado_ref, sup)), 4),
                    "u_paciente": round(p.u_paciente, 4),
                    "u_ojo": round(u_ojo, 4),
                    "u_paciente_farmaco": round(p.u_paciente_farmaco[f], 4),
                })

            # --- trayectoria del ojo ---
            cmt = float(np.clip(rng.normal(480, 90), 250, 850))
            av = float(np.clip(rng.uniform(0.1, 0.5), 0.02, 1.0))
            av_potencial = float(min(1.0, av + rng.uniform(0.1, 0.4)))
            cmt_prev: float | None = None
            av_prev: float | None = None

            farmaco = _elegir(rng, FARMACOS, PESOS_PRIMER_FARMACO)
            probados = [farmaco]
            estado = EstadoCiclo.inicio()
            semana, visita, intervalo_transcurrido = 0, 0, 0
            iny_total, iny_farmaco = 0, 0

            def fila(accion: str, inyectado: bool, activo_eval: bool,
                     intervalo_sig: int | None, est: EstadoCiclo, farm: str) -> dict:
                return {
                    "paciente_id": pid, "ojo": ojo, "edad": p.edad,
                    "diagnostico": p.diagnostico, "diabetes": p.diabetes,
                    "hipertension": p.hipertension, "acv_iam_reciente": p.acv_iam_reciente,
                    "tabaquismo": p.tabaquismo, "glaucoma": p.glaucoma,
                    "cristalino": p.cristalino, "tipo_mnv": p.tipo_mnv,
                    "visita_nro": visita, "semana": semana,
                    "farmaco": farm, "nro_farmaco_en_secuencia": len(probados),
                    "estado": est.etiqueta(), "fase": est.fase,
                    "intervalo_transcurrido_semanas": intervalo_transcurrido,
                    "av_decimal": round(av, 3), "cmt_um": round(cmt, 1),
                    "irf": int(irf), "srf": int(srf), "activo": int(activo_eval),
                    "accion": accion, "inyectado": int(inyectado),
                    "nro_inyeccion_farmaco": iny_farmaco, "nro_inyeccion_total": iny_total,
                    "intervalo_siguiente_semanas": intervalo_sig,
                    "desenlace_ciclo": "",
                }

            while True:
                if visita > 0:
                    if rng.random() < sup.prob_abandono_por_visita:
                        filas[-1]["desenlace_ciclo"] = "abandono"
                        break
                    if semana > HORIZONTE_SEMANAS:
                        filas[-1]["desenlace_ciclo"] = "censurado"
                        break

                visita += 1
                activo_latente = bool(rng.random() < _sigmoid(logit_actividad(p, u_ojo, farmaco, estado, sup)))
                irf, srf, cmt, av = _observables(rng, activo_latente, cmt, av, av_potencial)
                activo_eval = evaluar_actividad(irf, srf, cmt, cmt_prev, av, av_prev, sup)
                cmt_prev, av_prev = cmt, av

                dec = transicion(estado, activo_eval, sup)
                if dec.inyecta:
                    iny_total += 1
                    iny_farmaco += 1
                filas.append(fila(dec.accion, dec.inyecta, activo_eval,
                                  dec.intervalo_hasta_proxima, estado, farmaco))

                if dec.accion == ACCION_ESTABLE:
                    filas[-1]["desenlace_ciclo"] = "estable"
                    break

                if dec.accion == ACCION_SWITCH:
                    filas[-1]["desenlace_ciclo"] = "switch"
                    restantes = [f for f in FARMACOS if f not in probados]
                    if not restantes:
                        break  # sin opciones: el ojo sale del registro
                    # El nuevo fármaco arranca su carga en la MISMA visita
                    # (fila aparte, mismos observables, intervalo 0).
                    farmaco = _elegir(rng, restantes, PESOS_SWITCH)
                    probados.append(farmaco)
                    estado = EstadoCiclo.inicio()
                    iny_farmaco = 0
                    intervalo_transcurrido = 0
                    dec = transicion(estado, activo_eval, sup)
                    iny_total += 1
                    iny_farmaco += 1
                    filas.append(fila(dec.accion, True, activo_eval,
                                      dec.intervalo_hasta_proxima, estado, farmaco))

                estado = dec.siguiente
                intervalo_transcurrido = dec.intervalo_hasta_proxima
                semana += dec.intervalo_hasta_proxima

    return pd.DataFrame(filas), pd.DataFrame(verdad)


def resumen(historico: pd.DataFrame, verdad: pd.DataFrame) -> None:
    ciclos = historico[historico["desenlace_ciclo"] != ""]
    print(f"Filas: {len(historico)} | Pacientes: {historico['paciente_id'].nunique()} | "
          f"Ojos: {historico.groupby(['paciente_id', 'ojo']).ngroups}")
    print("\nDiagnósticos (pacientes):")
    print(historico.drop_duplicates("paciente_id")["diagnostico"].value_counts().to_string())
    print("\nDesenlace de ciclo por fármaco (proporción por fila):")
    print(pd.crosstab(ciclos["farmaco"], ciclos["desenlace_ciclo"], normalize="index").round(3).to_string())
    print("\nInyecciones por ojo:",
          historico.groupby(["paciente_id", "ojo"])["nro_inyeccion_total"].max().describe().round(1).to_dict())

    # Chequeo de sesgo de selección: respondedor latente de quienes llegan
    # a cada posición de la secuencia (más alto = mejor respondedor)
    u = verdad.drop_duplicates(["paciente_id", "ojo"])[["paciente_id", "ojo", "u_paciente"]]
    pos = historico.groupby(["paciente_id", "ojo"])["nro_farmaco_en_secuencia"].max().reset_index()
    m = pos.merge(u, on=["paciente_id", "ojo"])
    print("\nSesgo de selección -- u_paciente medio según a cuántos fármacos llegó el ojo:")
    print(m.groupby("nro_farmaco_en_secuencia")["u_paciente"].agg(["mean", "count"]).round(3).to_string())


if __name__ == "__main__":
    import sys
    salida = sys.argv[1] if len(sys.argv) > 1 else "."
    historico, verdad = generar()
    resumen(historico, verdad)
    historico.to_excel(f"{salida}/historico_rtu_SIMULADO.xlsx", index=False)
    verdad.to_excel(f"{salida}/verdad_oculta_rtu_SIMULADO.xlsx", index=False)
    print(f"\nGuardado en {salida}/historico_rtu_SIMULADO.xlsx y verdad_oculta_rtu_SIMULADO.xlsx")
