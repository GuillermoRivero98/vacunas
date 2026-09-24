"""
generar_datos_sinteticos.py

Genera un Excel histórico simulado, con:
  - el esquema BASE que exige motor_probabilidades.correr_pipeline
    (paciente_id, edad, comorbilidad, laboratorio, vacuna,
    nro_dosis_en_tratamiento, resultado) -- Camino A sigue funcionando
    exactamente igual, ignora las columnas nuevas que no conoce.
  - el esquema EXTENDIDO (diabetes, hipertension, acv_iam, alergias,
    tabaquismo, antecedentes_familiares, reaccion_adversa_previa,
    intervalo_semanas) -- red_bayesiana.py los detecta solo y arma el
    nodo compuesto Carga_comorbida en vez de caer al Comorbilidad
    simple. Ver docstring de red_bayesiana.py para el detalle.

Con SEÑAL REAL en todas las variables (a diferencia del
historico_vacunas_SINTETICO.xlsx original, puramente aleatorio) para
que la comparación de caminos en la presentación tenga algo genuino
que mostrar -- incluida la ventaja esperable del Camino B cuando usa
más predictores que el Camino A (que solo usa comorbilidad + edad).

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
    efecto_vacuna = {"VacunaA": 0.9, "VacunaB": -0.1, "VacunaC": -0.8, "VacunaD": 0.05}

    labs_por_vacuna = {
        "VacunaA": ["LabX", "LabY"],
        "VacunaB": ["LabX", "LabZ"],
        "VacunaC": ["LabY"],
        "VacunaD": ["LabX", "LabZ"],
    }
    efecto_laboratorio = {"LabX": 0.15, "LabY": 0.05, "LabZ": -0.35}

    filas = []
    paciente_id = 1

    for _ in range(n_pacientes):
        edad = int(np.clip(rng.normal(45, 22), 1, 95))

        # antecedentes granulares -- más prevalentes con la edad, cada
        # uno con su propio efecto (real) sobre la probabilidad de éxito
        p_edad = edad / 95
        diabetes = int(rng.random() < (0.05 + 0.30 * p_edad))
        hipertension = int(rng.random() < (0.08 + 0.40 * p_edad))
        acv_iam = int(rng.random() < (0.02 + 0.15 * p_edad))
        alergias = int(rng.random() < 0.18)              # independiente de la edad
        tabaquismo = int(rng.random() < 0.20)             # independiente de la edad
        antecedentes_familiares = int(rng.random() < 0.25)  # independiente de la edad
        # reacción adversa previa: más probable si tiene alergias
        reaccion_adversa_previa = int(rng.random() < (0.08 + 0.20 * alergias))

        # comorbilidad "resumen" (esquema base, para que Camino A siga
        # teniendo una señal real y no solo ruido): presente si tiene
        # alguno de los antecedentes graves
        comorbilidad = int(diabetes or hipertension or acv_iam)

        orden_intentado = list(rng.permutation(vacunas))

        for posicion, vacuna in enumerate(orden_intentado, start=1):
            laboratorio = rng.choice(labs_por_vacuna[vacuna])
            intervalo_semanas = int(np.clip(rng.normal(6, 3), 1, 20))

            logit = (
                -0.15  # intercepto base (un poco más alto que antes: se resta
                       # más abajo por los antecedentes específicos, en vez de
                       # un único término -0.55*comorbilidad)
                + efecto_vacuna[vacuna]
                + efecto_laboratorio[laboratorio]
                - 0.35 * diabetes
                - 0.30 * hipertension
                - 0.60 * acv_iam
                - 0.15 * tabaquismo
                - 0.05 * antecedentes_familiares
                - 0.50 * reaccion_adversa_previa
                - 0.02 * (intervalo_semanas - 6)
                - 0.006 * (edad - 45)
                - 0.12 * (posicion - 1)
            )
            p_exito = _sigmoid(logit)
            resultado = int(rng.random() < p_exito)

            filas.append({
                "paciente_id": paciente_id,
                "edad": edad,
                "comorbilidad": comorbilidad,
                "diabetes": diabetes,
                "hipertension": hipertension,
                "acv_iam": acv_iam,
                "alergias": alergias,
                "tabaquismo": tabaquismo,
                "antecedentes_familiares": antecedentes_familiares,
                "reaccion_adversa_previa": reaccion_adversa_previa,
                "intervalo_semanas": intervalo_semanas,
                "laboratorio": laboratorio,
                "vacuna": vacuna,
                "nro_dosis_en_tratamiento": posicion,
                "resultado": resultado,
            })

            if resultado == 1:
                break

        paciente_id += 1

    return pd.DataFrame(filas)


if __name__ == "__main__":
    df = generar(n_pacientes=1400)
    path_salida = "/mnt/user-data/outputs/historico_vacunas_SIMULADO_presentacion.xlsx"
    df.to_excel(path_salida, index=False)

    print(f"Filas generadas: {len(df)}  |  Pacientes: {df['paciente_id'].nunique()}")
    print(f"Columnas: {list(df.columns)}")
    print(f"Tasa de éxito global: {df['resultado'].mean():.3f}")
    print()
    print("Tasa de éxito por vacuna:")
    print(df.groupby("vacuna")["resultado"].agg(["mean", "count"]))
    print()
    print("Tasa de éxito por antecedente (cada uno, presente vs ausente):")
    for col in ["diabetes", "hipertension", "acv_iam", "alergias", "tabaquismo",
                "antecedentes_familiares", "reaccion_adversa_previa"]:
        print(f"  {col:28s}", df.groupby(col)["resultado"].mean().round(3).to_dict())
    print(f"\nGuardado en: {path_salida}")
