"""
generar_datos_sinteticos.py

Genera un Excel histórico simulado, mismo esquema que ya exige
motor_probabilidades.correr_pipeline (paciente_id, edad, comorbilidad,
laboratorio, vacuna, nro_dosis_en_tratamiento, resultado), pero con
SEÑAL REAL -- a diferencia del historico_vacunas_SINTETICO.xlsx original
(puramente aleatorio, por eso Camino A y B empataron en el backtest),
acá cada vacuna/laboratorio/comorbilidad/edad influye de verdad en la
probabilidad de éxito, para que la comparación de caminos en la
presentación tenga algo real que mostrar.

Uso exclusivo para pruebas/demo -- NO son datos de pacientes reales.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RNG_SEED = 2026


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def generar(n_pacientes: int = 1400, seed: int = RNG_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    vacunas = ["VacunaA", "VacunaB", "VacunaC", "VacunaD"]
    # efecto base de cada vacuna (en escala logit) -- VacunaA es la más
    # efectiva, VacunaC la menos, con superposición realista entre B y D
    efecto_vacuna = {"VacunaA": 0.9, "VacunaB": -0.1, "VacunaC": -0.8, "VacunaD": 0.05}

    # cada vacuna sale de un subconjunto de laboratorios (igual patrón
    # que el Excel real: no todas las combinaciones existen)
    labs_por_vacuna = {
        "VacunaA": ["LabX", "LabY"],
        "VacunaB": ["LabX", "LabZ"],
        "VacunaC": ["LabY"],
        "VacunaD": ["LabX", "LabZ"],
    }
    # efecto propio del laboratorio (independiente de la vacuna) --
    # LabZ tiene peor performance general (ej. cadena de frío menos
    # confiable), efecto pequeño pero real
    efecto_laboratorio = {"LabX": 0.15, "LabY": 0.05, "LabZ": -0.35}

    filas = []
    paciente_id = 1

    for _ in range(n_pacientes):
        edad = int(np.clip(rng.normal(45, 22), 1, 95))
        comorbilidad = int(rng.random() < (0.15 + 0.5 * (edad / 95)))  # más prevalente con edad

        # cuántas vacunas necesita probar este paciente hasta que una
        # "pegue" (o se agoten las 4 disponibles) -- se determina
        # simulando el proceso real, no de antemano
        orden_intentado = list(rng.permutation(vacunas))

        for posicion, vacuna in enumerate(orden_intentado, start=1):
            laboratorio = rng.choice(labs_por_vacuna[vacuna])

            logit = (
                -0.3  # intercepto base
                + efecto_vacuna[vacuna]
                + efecto_laboratorio[laboratorio]
                - 0.55 * comorbilidad          # comorbilidad baja la probabilidad
                - 0.006 * (edad - 45)           # edad mayor levemente peor (leve)
                - 0.12 * (posicion - 1)          # intentos posteriores del mismo tratamiento, algo más difíciles
            )
            p_exito = _sigmoid(logit)
            resultado = int(rng.random() < p_exito)

            filas.append({
                "paciente_id": paciente_id,
                "edad": edad,
                "comorbilidad": comorbilidad,
                "laboratorio": laboratorio,
                "vacuna": vacuna,
                "nro_dosis_en_tratamiento": posicion,
                "resultado": resultado,
            })

            if resultado == 1:
                break  # tratamiento completado, no prueba las demás

        paciente_id += 1

    return pd.DataFrame(filas)


if __name__ == "__main__":
    df = generar(n_pacientes=1400)
    path_salida = "/mnt/user-data/outputs/historico_vacunas_SIMULADO_presentacion.xlsx"
    df.to_excel(path_salida, index=False)

    print(f"Filas generadas: {len(df)}")
    print(f"Pacientes: {df['paciente_id'].nunique()}")
    print(f"Tasa de éxito global: {df['resultado'].mean():.3f}")
    print()
    print("Tasa de éxito por vacuna (la señal real que metimos):")
    print(df.groupby("vacuna")["resultado"].agg(["mean", "count"]))
    print()
    print("Tasa de éxito por laboratorio:")
    print(df.groupby("laboratorio")["resultado"].agg(["mean", "count"]))
    print()
    print("Tasa de éxito por comorbilidad:")
    print(df.groupby("comorbilidad")["resultado"].agg(["mean", "count"]))
    print(f"\nGuardado en: {path_salida}")
