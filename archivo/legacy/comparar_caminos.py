"""
comparar_caminos.py

Compara el Camino A (motor_probabilidades.py: Beta posterior sobre
"casos similares" armados a mano) contra el Camino B (red_bayesiana.py)
de dos formas:

  1. comparar_para_caso(): para UN paciente puntual, muestra lado a lado
     el orden sugerido y las p_i de cada camino (chequeo cualitativo
     rápido, el que ya veníamos haciendo a mano).

  2. backtest(): evalúa ambos caminos sobre TODO el histórico con una
     partición train/test POR PACIENTE (nunca por fila -- un mismo
     paciente puede tener varias filas/intentos, y mezclarlas entre
     train y test filtraría información y infla artificialmente la
     performance reportada). Métrica: Brier score (error cuadrático
     medio entre p_i estimado y el resultado real 0/1) -- mide
     calibración, no solo si el ranking de vacunas quedó bien, que es
     lo que realmente importa acá porque p_i entra directo en la
     fórmula de dosis esperadas E[N], no en una clasificación.

No decide nada: es una herramienta de evaluación offline para el
informe, se corre aparte, no se enchufa a la API.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass

from motor_probabilidades import (
    filtrar_similares,
    estimar_beta_por_vacuna,
    esperanza_dosis,
)
from red_bayesiana import (
    ConfigDiscretizacion,
    preparar_dataframe,
    entrenar_red,
    estimar_p_por_vacuna,
    VariableElimination,
    estimar_probabilidad_bayesiana,
)


# ---------------------------------------------------------------------------
# 1. Comparación cualitativa para un caso puntual
# ---------------------------------------------------------------------------

def comparar_para_caso(path_excel: str, caso: dict, margen_edad: int = 10) -> pd.DataFrame:
    """Devuelve una tabla con p_i de Camino A y Camino B lado a lado
    para el mismo caso, más el orden sugerido y E[N] de cada uno."""
    df = pd.read_excel(path_excel)
    vacunas = sorted(df["vacuna"].unique())

    # --- Camino A ---
    df_similares = filtrar_similares(df, caso, margen_edad)
    resultados_a = estimar_beta_por_vacuna(df_similares, df)
    p_a = {r.vacuna: r.p_mean for r in resultados_a}
    orden_a = [r.vacuna for r in resultados_a]  # ya viene ordenado desc

    # --- Camino B ---
    df_prep, estructura = preparar_dataframe(df)
    modelo = entrenar_red(df_prep, estructura)
    p_b = estimar_p_por_vacuna(modelo, caso, vacunas)
    orden_b = sorted(vacunas, key=lambda v: p_b[v], reverse=True)

    tabla = pd.DataFrame({
        "vacuna": vacunas,
        "p_camino_A": [round(p_a.get(v, float("nan")), 3) for v in vacunas],
        "p_camino_B": [round(p_b[v], 3) for v in vacunas],
    }).sort_values("p_camino_B", ascending=False).reset_index(drop=True)

    print(f"Caso: {caso}")
    print(tabla.to_string(index=False))
    print()
    print(f"Orden sugerido Camino A: {' -> '.join(orden_a)}")
    print(f"  E[dosis]: {esperanza_dosis(orden_a, p_a):.3f}")
    print(f"Orden sugerido Camino B: {' -> '.join(orden_b)}")
    print(f"  E[dosis]: {esperanza_dosis(orden_b, p_b):.3f}")

    return tabla


# ---------------------------------------------------------------------------
# 2. Backtest con partición train/test POR PACIENTE
# ---------------------------------------------------------------------------

@dataclass
class ResultadoBacktest:
    brier_camino_a: float
    brier_camino_b: float
    n_predicciones: int
    n_pacientes_train: int
    n_pacientes_test: int
    detalle: pd.DataFrame


def backtest(
    path_excel: str,
    frac_test: float = 0.3,
    margen_edad: int = 10,
    seed: int = 12345,
) -> ResultadoBacktest:
    """
    Separa pacientes en train/test, entrena/estima ambos caminos SOLO
    con train, y para cada fila (paciente, vacuna, resultado real) del
    test evalúa qué tan cerca estuvo cada camino del resultado real
    observado (0 o 1) -- Brier score = mean((p_estimado - resultado)^2),
    más bajo es mejor.
    """
    df = pd.read_excel(path_excel)
    rng = np.random.default_rng(seed)

    pacientes = df["paciente_id"].unique()
    rng.shuffle(pacientes)
    corte = int(len(pacientes) * (1 - frac_test))
    pacientes_train = set(pacientes[:corte])
    pacientes_test = set(pacientes[corte:])

    df_train = df[df["paciente_id"].isin(pacientes_train)].copy()
    df_test = df[df["paciente_id"].isin(pacientes_test)].copy()

    # --- Entrenar Camino B una sola vez sobre train ---
    df_train_prep, estructura = preparar_dataframe(df_train)
    modelo_b = entrenar_red(df_train_prep, estructura)
    inferencia_b = VariableElimination(modelo_b)
    config = ConfigDiscretizacion()

    # --- Para cada paciente de test, tomar su primera fila como "caso"
    #     (edad, comorbilidad) -- igual que un paciente nuevo llegando
    #     con esos datos, sin importar qué haya pasado en sus intentos
    #     posteriores dentro del mismo tratamiento.
    filas = []
    casos_test = (
        df_test.sort_values("nro_dosis_en_tratamiento")
        .groupby("paciente_id")
        .first()[["edad", "comorbilidad"]]
    )

    for paciente_id, fila_paciente in df_test.iterrows():
        caso = {
            "edad": int(casos_test.loc[fila_paciente["paciente_id"], "edad"]),
            "comorbilidad": int(casos_test.loc[fila_paciente["paciente_id"], "comorbilidad"]),
        }
        vacuna = fila_paciente["vacuna"]
        resultado_real = int(fila_paciente["resultado"])

        # Camino A: casos similares dentro de TRAIN únicamente
        df_similares_train = filtrar_similares(df_train, caso, margen_edad)
        if len(df_similares_train) == 0:
            continue  # sin base para estimar, se descarta esta fila
        resultados_a = estimar_beta_por_vacuna(df_similares_train, df_train)
        p_a_dict = {r.vacuna: r.p_mean for r in resultados_a}
        if vacuna not in p_a_dict:
            continue  # esa vacuna no tiene casos similares en train
        p_a = p_a_dict[vacuna]

        # Camino B: red ya entrenada solo con train
        try:
            p_b = estimar_probabilidad_bayesiana(modelo_b, caso, vacuna, config, inferencia_b)
        except Exception:
            continue  # categoría no vista en train (edad fuera de rango, etc.)

        filas.append({
            "paciente_id": fila_paciente["paciente_id"],
            "vacuna": vacuna,
            "resultado_real": resultado_real,
            "p_camino_A": p_a,
            "p_camino_B": p_b,
            "error2_A": (p_a - resultado_real) ** 2,
            "error2_B": (p_b - resultado_real) ** 2,
        })

    detalle = pd.DataFrame(filas)
    if detalle.empty:
        raise RuntimeError(
            "Backtest sin predicciones válidas -- probablemente el dataset "
            "es muy chico para esta partición train/test. Reducir frac_test "
            "o usar más margen_edad."
        )

    return ResultadoBacktest(
        brier_camino_a=detalle["error2_A"].mean(),
        brier_camino_b=detalle["error2_B"].mean(),
        n_predicciones=len(detalle),
        n_pacientes_train=len(pacientes_train),
        n_pacientes_test=len(pacientes_test),
        detalle=detalle,
    )


if __name__ == "__main__":
    PATH_EXCEL = "/mnt/user-data/uploads/historico_vacunas_SINTETICO.xlsx"

    print("=" * 70)
    print("1) Comparación cualitativa para un caso puntual")
    print("=" * 70)
    comparar_para_caso(PATH_EXCEL, {"edad": 30, "comorbilidad": 0})

    print()
    print("=" * 70)
    print("2) Backtest train/test por paciente (calibración, Brier score)")
    print("=" * 70)
    resultado = backtest(PATH_EXCEL, frac_test=0.3)
    print(f"Pacientes train: {resultado.n_pacientes_train}  |  "
          f"Pacientes test: {resultado.n_pacientes_test}")
    print(f"Predicciones evaluadas: {resultado.n_predicciones}")
    print(f"Brier score Camino A (Beta posterior): {resultado.brier_camino_a:.4f}")
    print(f"Brier score Camino B (red bayesiana):   {resultado.brier_camino_b:.4f}")
    print("(más bajo = mejor calibrado; 0.25 es el score de predecir 0.5 siempre)")
