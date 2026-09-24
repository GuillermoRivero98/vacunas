"""
pipeline_bayesiano.py

Envoltorio de producción para el Camino B (red_bayesiana.py), pensado
para enchufarse a api.py como un endpoint nuevo, en paralelo al
Camino A existente (motor_probabilidades.py) -- no lo reemplaza.

Devuelve el resultado en la MISMA FORMA que resultado_a_dict() del
Camino A (mismas claves), para que el frontend pueda reusar
ResultadoView.tsx con cambios mínimos si en algún momento se decide
exponer Camino B en la UI. Ver discusión de diseño: por ahora la
recomendación es UN método primario en pantalla, con esto disponible
como comparación/auditoría, no como 3 números sueltos para que el
médico elija.

IC95% y comparaciones pareadas P(A>B) se calculan por BOOTSTRAP sobre
PACIENTES (no filas -- un mismo paciente puede tener varias filas/
intentos, hay que remuestrear la unidad de paciente para no inflar la
precisión aparente), reentrenando la red en cada remuestreo. Es el
análogo, para una red bayesiana, del muestreo de la posterior Beta que
ya hace motor_probabilidades.prob_pares_montecarlo -- acá no hay una
posterior cerrada por vacuna, así que se aproxima por bootstrap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from motor_probabilidades import esperanza_dosis
from red_bayesiana import (
    ConfigDiscretizacion,
    preparar_dataframe,
    entrenar_red,
    estimar_p_por_vacuna,
)

N_BOOTSTRAP_DEFAULT = 40  # compromiso entre precisión del IC y tiempo de respuesta
SEED_BOOTSTRAP = 20260921  # fija, para reproducibilidad (auditable como el resto del sistema)


@dataclass
class ResultadoVacunaBayesiana:
    vacuna: str
    p_mean: float
    ci_low: float
    ci_high: float


@dataclass
class ResultadoPipelineBayesiano:
    caso: dict
    n_entrenamiento: int  # pacientes usados para entrenar (todo el histórico, no un subgrupo filtrado)
    resultados: list[ResultadoVacunaBayesiana] = field(default_factory=list)
    orden_optimo: list[str] = field(default_factory=list)
    en_orden_optimo: float = 0.0
    en_peor_orden: float = 0.0
    prob_pares: pd.DataFrame = None
    notas_estructura: list[str] = field(default_factory=list)


def _entrenar_y_estimar(df_hist: pd.DataFrame, caso: dict, vacunas: list[str], config: ConfigDiscretizacion):
    df_prep, estructura = preparar_dataframe(df_hist, config)
    modelo = entrenar_red(df_prep, estructura)
    p_por_vacuna = estimar_p_por_vacuna(modelo, caso, vacunas, config)
    return p_por_vacuna, estructura


def correr_pipeline_bayesiano(
    path_excel: str,
    caso: dict,
    n_bootstrap: int = N_BOOTSTRAP_DEFAULT,
) -> ResultadoPipelineBayesiano:
    df = pd.read_excel(path_excel)

    columnas_esperadas = {"paciente_id", "edad", "laboratorio", "vacuna",
                           "nro_dosis_en_tratamiento", "resultado"}
    faltantes = columnas_esperadas - set(df.columns)
    if faltantes:
        raise ValueError(f"Faltan columnas en el Excel: {faltantes}")

    config = ConfigDiscretizacion()
    vacunas = sorted(df["vacuna"].unique())

    # --- Estimación puntual (todo el histórico) ---
    p_estimado, estructura = _entrenar_y_estimar(df, caso, vacunas, config)
    orden_optimo = sorted(vacunas, key=lambda v: p_estimado[v], reverse=True)

    en_optimo = esperanza_dosis(orden_optimo, p_estimado)
    en_peor = esperanza_dosis(list(reversed(orden_optimo)), p_estimado)

    # --- Bootstrap por paciente para IC95% y comparaciones pareadas ---
    pacientes = df["paciente_id"].unique()
    rng = np.random.default_rng(SEED_BOOTSTRAP)
    muestras_por_vacuna = {v: [] for v in vacunas}

    for _ in range(n_bootstrap):
        pacientes_remuestreados = rng.choice(pacientes, size=len(pacientes), replace=True)
        df_boot = df[df["paciente_id"].isin(set(pacientes_remuestreados))]
        try:
            p_boot, _ = _entrenar_y_estimar(df_boot, caso, vacunas, config)
        except Exception:
            continue  # remuestreo degenerado (p.ej. le faltó una categoría) -- se descarta
        for v in vacunas:
            if v in p_boot:
                muestras_por_vacuna[v].append(p_boot[v])

    resultados = []
    for v in vacunas:
        muestras = np.array(muestras_por_vacuna[v]) if muestras_por_vacuna[v] else np.array([p_estimado[v]])
        ci_low, ci_high = np.percentile(muestras, [2.5, 97.5])
        resultados.append(ResultadoVacunaBayesiana(
            vacuna=v, p_mean=p_estimado[v], ci_low=float(ci_low), ci_high=float(ci_high),
        ))
    resultados.sort(key=lambda r: r.p_mean, reverse=True)

    filas_pares = []
    for i, vi in enumerate(vacunas):
        for vj in vacunas[i + 1:]:
            mi, mj = muestras_por_vacuna[vi], muestras_por_vacuna[vj]
            n_comparable = min(len(mi), len(mj))
            if n_comparable == 0:
                prob = float(p_estimado[vi] > p_estimado[vj])
            else:
                prob = float(np.mean(np.array(mi[:n_comparable]) > np.array(mj[:n_comparable])))
            filas_pares.append({"vacuna_A": vi, "vacuna_B": vj, "P(A > B)": round(prob, 3)})

    return ResultadoPipelineBayesiano(
        caso=caso,
        n_entrenamiento=len(pacientes),
        resultados=resultados,
        orden_optimo=orden_optimo,
        en_orden_optimo=en_optimo,
        en_peor_orden=en_peor,
        prob_pares=pd.DataFrame(filas_pares),
        notas_estructura=estructura.notas,
    )


def resultado_bayesiano_a_dict(resultado: ResultadoPipelineBayesiano) -> dict:
    """Mismo formato que motor_probabilidades.resultado_a_dict(), para
    que el frontend pueda reusar el mismo tipo ResultadoCalculo (ver
    types.ts) sin cambios -- k/n no aplican acá (no hay conteo discreto
    de 'casos similares', la red usa todo el histórico ponderado), se
    devuelven como null."""
    return {
        "metodo": "red_bayesiana",
        "n_similares": resultado.n_entrenamiento,  # todo el histórico, no un subgrupo filtrado
        "orden_sugerido": resultado.orden_optimo,
        "dosis_esperadas_orden_optimo": round(resultado.en_orden_optimo, 3),
        "dosis_esperadas_peor_orden": round(resultado.en_peor_orden, 3),
        "vacunas": [
            {
                "vacuna": r.vacuna, "p_estimado": round(r.p_mean, 3),
                "ic95": [round(r.ci_low, 3), round(r.ci_high, 3)],
                "k": None, "n": None, "n_historico_total": resultado.n_entrenamiento,
            }
            for r in resultado.resultados
        ],
        "comparaciones_pareadas": resultado.prob_pares.to_dict(orient="records"),
        "alerta_estratificacion": None,
        "notas_estructura": resultado.notas_estructura,
    }


if __name__ == "__main__":
    caso_ejemplo = {"edad": 70, "comorbilidad": 1}
    resultado = correr_pipeline_bayesiano(
        "historico_vacunas_SIMULADO_presentacion.xlsx", caso_ejemplo, n_bootstrap=40,
    )

    print(f"Caso: {resultado.caso}")
    print(f"Pacientes usados para entrenar: {resultado.n_entrenamiento}")
    print()
    for r in resultado.resultados:
        print(f"  {r.vacuna:10s}  E[p]={r.p_mean:.3f}  IC95%=[{r.ci_low:.3f}, {r.ci_high:.3f}]")
    print()
    print(f"Orden sugerido: {' -> '.join(resultado.orden_optimo)}")
    print(f"E[dosis] orden óptimo: {resultado.en_orden_optimo:.3f}")
    print(f"E[dosis] peor orden:   {resultado.en_peor_orden:.3f}")
    print()
    print("Comparaciones pareadas (bootstrap):")
    print(resultado.prob_pares.to_string(index=False))
