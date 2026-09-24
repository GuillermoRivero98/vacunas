"""
red_bayesiana.py

Camino B del sistema de optimización de orden de vacunación:
estima la probabilidad p_i de éxito de cada vacuna candidata usando una
red bayesiana (pgmpy) sobre variables del paciente, en vez del posterior
Beta simple sobre un grupo de "casos similares" armado a mano (Camino A,
en motor_probabilidades.py).

Principio de diseño (igual que el Camino A): este módulo SOLO estima p_i.
No decide el orden de vacunación -- eso lo sigue haciendo el motor de
Markov absorbente ya existente (esperanza_dosis en motor_probabilidades.py,
reutilizada acá sin modificarla).

ESQUEMA BASE CONFIRMADO (Excel de producción / historico_vacunas_SINTETICO.xlsx,
mismas columnas que ya valida motor_probabilidades.correr_pipeline):
    paciente_id, edad, comorbilidad, laboratorio, vacuna,
    nro_dosis_en_tratamiento, resultado

ESQUEMA EXTENDIDO (opcional, todavía no confirmado contra el dataset
real -- inspirado en sistemas publicados de manejo de tratamientos
crónicos por dosis, ej. RetinApp para terapia anti-VEGF en Chile, que
trackea antecedentes mórbidos granulares en vez de un flag único):
    diabetes, hipertension, duracion_hta_anios, acv_iam, alergias,
    tabaquismo, antecedentes_familiares, reaccion_adversa_previa,
    intervalo_semanas
Todas OPCIONALES: si una columna no está en el Excel, ese nodo
simplemente no se agrega al DAG (ver construir_estructura()) -- el
código corre igual contra el esquema base de 7 columnas y se
enriquece solo si el Excel real trae más.

LIMITACIÓN DOCUMENTADA (no implementada a propósito): RetinApp trackea
un outcome CONTINUO y longitudinal (AV/CMT con Δ en cada control), no
un éxito/fracaso binario por dosis. El motor de Markov absorbente de
este sistema necesita justamente un resultado binario por intento para
calcular E[N] -- forzar biomarcadores continuos acá requeriría cambiar
de arquitectura (algo tipo modelo de espacio de estados / HMM en vez de
una red bayesiana estática), fuera de alcance de este trabajo. Se deja
anotado como extensión futura.

DAG BASE (siempre presente):

    Laboratorio ─────────────┐
    Vacuna ────────────────── ┼──► Exito
    Nro_dosis_en_tratamiento ┘

DAG con antecedentes granulares (si el Excel los trae -- ver
construir_estructura() para el detalle de qué activa cada nodo):

    Edad ──► Carga_comorbida ──┐
                                 │
    Reaccion_adversa_previa ────┤
    Intervalo_semanas ──────────┼──► Exito
    Laboratorio ─────────────── ┤
    Vacuna ─────────────────────┤
    Nro_dosis_en_tratamiento ───┘

Si NINGUNA columna granular de antecedentes está pero sí existe la
columna `comorbilidad` (0/1) del esquema base, se usa esa como nodo
`Comorbilidad` directo (comportamiento de antes, sin cambios).

Por qué un nodo compuesto `Carga_comorbida` y no 6-7 padres directos
de Exito: cada antecedente nuevo como padre directo MULTIPLICA la
cantidad de celdas de la CPT de Exito (con Vacuna x Laboratorio x
Nro_dosis ya hay ~48 combinaciones; sumar 6 binarios más la lleva a
miles de celdas, la mayoría sin datos suficientes con un histórico de
~3000 filas). Agruparlos en una variable resumen (Bajo/Medio/Alto,
mismo criterio que un índice de Charlson) mantiene la CPT manejable.
Esto es una simplificación deliberada, documentada acá y en el informe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from pgmpy.estimators import BayesianEstimator
from pgmpy.inference import VariableElimination
from pgmpy.models import DiscreteBayesianNetwork


# ---------------------------------------------------------------------------
# 1. Configuración de discretización (bins editables sin tocar el resto)
# ---------------------------------------------------------------------------

@dataclass
class ConfigDiscretizacion:
    bins_edad: list[int] = field(
        default_factory=lambda: [0, 18, 31, 46, 61, 76, 150]
    )
    etiquetas_edad: list[str] = field(
        default_factory=lambda: ["0-17", "18-30", "31-45", "46-60", "61-75", "76+"]
    )
    # Bins para el conteo de antecedentes positivos -> Carga_comorbida
    bins_carga_comorbida: list[int] = field(default_factory=lambda: [0, 2, 4, 10])
    etiquetas_carga_comorbida: list[str] = field(
        default_factory=lambda: ["Baja", "Media", "Alta"]
    )
    # Bins de intervalo entre dosis (semanas) -- ajustar según protocolo real
    bins_intervalo: list[int] = field(default_factory=lambda: [0, 4, 8, 12, 999])
    etiquetas_intervalo: list[str] = field(
        default_factory=lambda: ["<=4", "5-8", "9-12", "12+"]
    )

    def discretizar_edad(self, serie: pd.Series) -> pd.Series:
        return pd.cut(serie, bins=self.bins_edad, labels=self.etiquetas_edad, right=False)

    def discretizar_carga_comorbida(self, serie_conteo: pd.Series) -> pd.Series:
        return pd.cut(
            serie_conteo, bins=self.bins_carga_comorbida,
            labels=self.etiquetas_carga_comorbida, right=False,
        )

    def discretizar_intervalo(self, serie_semanas: pd.Series) -> pd.Series:
        return pd.cut(
            serie_semanas, bins=self.bins_intervalo,
            labels=self.etiquetas_intervalo, right=True,
        )


# Columnas granulares de antecedentes que, si están presentes (aunque
# sea una sola), activan el nodo compuesto Carga_comorbida en vez del
# Comorbilidad binario simple. Todas se leen como 0/1 (sí/no).
COLUMNAS_ANTECEDENTES_GRANULARES = [
    "diabetes", "hipertension", "acv_iam", "alergias",
    "tabaquismo", "antecedentes_familiares",
]


# ---------------------------------------------------------------------------
# 2. Estructura del DAG -- se arma dinámicamente según qué columnas
#    opcionales estén presentes en el Excel (ver docstring del módulo).
# ---------------------------------------------------------------------------

@dataclass
class EstructuraRed:
    aristas: list[tuple[str, str]]
    variables: list[str]
    usa_carga_comorbida: bool
    notas: list[str]  # qué se detectó / qué se usó de fallback, para trazabilidad


def construir_estructura(columnas_disponibles: set[str]) -> EstructuraRed:
    aristas = [
        ("Laboratorio", "Exito"),
        ("Vacuna", "Exito"),
        ("Nro_dosis_en_tratamiento", "Exito"),
    ]
    variables = ["Laboratorio", "Vacuna", "Nro_dosis_en_tratamiento", "Exito"]
    notas = []

    tiene_granulares = any(c in columnas_disponibles for c in COLUMNAS_ANTECEDENTES_GRANULARES)

    usa_carga_comorbida = False
    if tiene_granulares:
        detectadas = [c for c in COLUMNAS_ANTECEDENTES_GRANULARES if c in columnas_disponibles]
        notas.append(
            f"Antecedentes granulares detectados: {detectadas} -> nodo compuesto "
            f"Carga_comorbida (Baja/Media/Alta)."
        )
        aristas += [("Edad", "Carga_comorbida"), ("Carga_comorbida", "Exito")]
        variables += ["Edad", "Carga_comorbida"]
        usa_carga_comorbida = True
    elif "comorbilidad" in columnas_disponibles:
        notas.append(
            "Sin antecedentes granulares -- usando columna 'comorbilidad' "
            "(esquema base) como nodo Comorbilidad directo."
        )
        aristas += [("Edad", "Comorbilidad"), ("Comorbilidad", "Exito")]
        variables += ["Edad", "Comorbilidad"]
    else:
        notas.append(
            "ADVERTENCIA: ni antecedentes granulares ni 'comorbilidad' están "
            "presentes -- el DAG queda sin ninguna variable de morbilidad."
        )

    if "reaccion_adversa_previa" in columnas_disponibles:
        notas.append("Columna 'reaccion_adversa_previa' detectada -> nodo agregado.")
        aristas.append(("Reaccion_adversa_previa", "Exito"))
        variables.append("Reaccion_adversa_previa")

    if "intervalo_semanas" in columnas_disponibles:
        notas.append("Columna 'intervalo_semanas' detectada -> nodo agregado.")
        aristas.append(("Intervalo_semanas", "Exito"))
        variables.append("Intervalo_semanas")

    return EstructuraRed(aristas, variables, usa_carga_comorbida, notas)


# ---------------------------------------------------------------------------
# 3. Preparación del DataFrame (discretización + armado dinámico del DAG)
# ---------------------------------------------------------------------------

COLUMNAS_BASE_REQUERIDAS = {"edad", "laboratorio", "vacuna", "nro_dosis_en_tratamiento", "resultado"}


def preparar_dataframe(
    df_crudo: pd.DataFrame,
    config: Optional[ConfigDiscretizacion] = None,
) -> tuple[pd.DataFrame, EstructuraRed]:
    """
    Toma el histórico crudo, detecta qué columnas opcionales están
    presentes, arma la estructura del DAG en consecuencia, y devuelve
    el DataFrame ya discretizado junto con la EstructuraRed usada
    (necesaria para entrenar_red).
    """
    config = config or ConfigDiscretizacion()

    faltantes = COLUMNAS_BASE_REQUERIDAS - set(df_crudo.columns)
    if faltantes:
        raise ValueError(
            f"Faltan columnas base en el histórico: {faltantes}. "
            "Mismo esquema mínimo que exige motor_probabilidades.correr_pipeline."
        )
    if "comorbilidad" not in df_crudo.columns and not any(
        c in df_crudo.columns for c in COLUMNAS_ANTECEDENTES_GRANULARES
    ):
        raise ValueError(
            "El histórico no tiene 'comorbilidad' ni ningún antecedente granular "
            f"({COLUMNAS_ANTECEDENTES_GRANULARES}) -- se necesita al menos uno."
        )

    estructura = construir_estructura(set(df_crudo.columns))

    df = pd.DataFrame(index=df_crudo.index)
    df["Edad"] = config.discretizar_edad(df_crudo["edad"]) if "Edad" in estructura.variables else None
    df["Laboratorio"] = df_crudo["laboratorio"].astype(str)
    df["Vacuna"] = df_crudo["vacuna"].astype(str)
    df["Nro_dosis_en_tratamiento"] = df_crudo["nro_dosis_en_tratamiento"].astype(str)
    df["Exito"] = df_crudo["resultado"].astype(int)

    if estructura.usa_carga_comorbida:
        conteo = sum(
            df_crudo[c].astype(int) for c in COLUMNAS_ANTECEDENTES_GRANULARES
            if c in df_crudo.columns
        )
        df["Carga_comorbida"] = config.discretizar_carga_comorbida(conteo)
    elif "Comorbilidad" in estructura.variables:
        df["Comorbilidad"] = df_crudo["comorbilidad"].astype(str)

    if "Reaccion_adversa_previa" in estructura.variables:
        df["Reaccion_adversa_previa"] = df_crudo["reaccion_adversa_previa"].astype(str)

    if "Intervalo_semanas" in estructura.variables:
        df["Intervalo_semanas"] = config.discretizar_intervalo(df_crudo["intervalo_semanas"])

    return df[estructura.variables], estructura


# ---------------------------------------------------------------------------
# 4. Entrenamiento
# ---------------------------------------------------------------------------

def entrenar_red(
    df_preparado: pd.DataFrame,
    estructura: EstructuraRed,
    pseudo_conteos: float = 1.0,
) -> DiscreteBayesianNetwork:
    """
    Entrena con BayesianEstimator y prior Dirichlet uniforme
    (equivalente multivariado del Beta(1,1) no informativo del Camino A).

    Nota de versión: en pgmpy 1.x, DiscreteBayesianNetwork.fit() ya no
    acepta prior_type/pseudo_counts directamente -- se estiman las CPDs
    con BayesianEstimator aparte y se agregan al modelo.
    """
    modelo = DiscreteBayesianNetwork(estructura.aristas)
    estimador = BayesianEstimator(modelo, df_preparado)
    cpds = estimador.get_parameters(prior_type="dirichlet", pseudo_counts=pseudo_conteos)
    modelo.add_cpds(*cpds)
    modelo.check_model()
    return modelo


# ---------------------------------------------------------------------------
# 5. Inferencia -- interfaz compatible con el motor de Markov
# ---------------------------------------------------------------------------

def _armar_evidencia(
    caso: dict,
    vacuna_candidata: str,
    nodos_modelo: set[str],
    config: ConfigDiscretizacion,
) -> dict:
    """
    Arma el dict de evidencia SOLO con los nodos que el modelo
    realmente tiene (según qué columnas tenía el Excel de entrenamiento)
    y que el `caso` del paciente nuevo provee. Un nodo del modelo sin
    dato en `caso` se deja fuera de la evidencia (pgmpy lo marginaliza).

    `caso` acepta, todos opcionales salvo edad:
      edad (obligatorio), comorbilidad (0/1),
      diabetes, hipertension, acv_iam, alergias, tabaquismo,
      antecedentes_familiares (0/1 cada uno, para Carga_comorbida),
      reaccion_adversa_previa (0/1), intervalo_semanas (numérico)
    """
    evidencia: dict = {"Vacuna": vacuna_candidata, "Nro_dosis_en_tratamiento": "1"}

    if "Edad" in nodos_modelo:
        evidencia["Edad"] = config.discretizar_edad(pd.Series([caso["edad"]])).iloc[0]

    if "Carga_comorbida" in nodos_modelo:
        valores = [caso[c] for c in COLUMNAS_ANTECEDENTES_GRANULARES if c in caso]
        if valores:
            conteo = sum(int(v) for v in valores)
            evidencia["Carga_comorbida"] = config.discretizar_carga_comorbida(
                pd.Series([conteo])
            ).iloc[0]
        # si no se proveyó ningún antecedente del paciente nuevo, se deja
        # sin evidencia -- pgmpy marginaliza sobre Carga_comorbida
    elif "Comorbilidad" in nodos_modelo and "comorbilidad" in caso:
        evidencia["Comorbilidad"] = str(caso["comorbilidad"])

    if "Reaccion_adversa_previa" in nodos_modelo and "reaccion_adversa_previa" in caso:
        evidencia["Reaccion_adversa_previa"] = str(caso["reaccion_adversa_previa"])

    if "Intervalo_semanas" in nodos_modelo and "intervalo_semanas" in caso:
        evidencia["Intervalo_semanas"] = config.discretizar_intervalo(
            pd.Series([caso["intervalo_semanas"]])
        ).iloc[0]

    return evidencia


def estimar_probabilidad_bayesiana(
    modelo: DiscreteBayesianNetwork,
    caso: dict,
    vacuna_candidata: str,
    config: Optional[ConfigDiscretizacion] = None,
    inferencia: Optional[VariableElimination] = None,
) -> float:
    """
    Devuelve P(Exito=1 | evidencia) para un paciente nuevo y una vacuna
    candidata. Nro_dosis_en_tratamiento se fija en "1" (orden inicial,
    paciente que todavía no probó ninguna vacuna de este tratamiento).
    Laboratorio NO se pasa como evidencia -- se marginaliza, porque para
    un paciente nuevo no se sabe qué laboratorio va a suministrar la
    vacuna elegida.
    """
    config = config or ConfigDiscretizacion()
    inferencia = inferencia or VariableElimination(modelo)
    nodos_modelo = set(modelo.nodes())

    evidencia = _armar_evidencia(caso, vacuna_candidata, nodos_modelo, config)

    resultado = inferencia.query(variables=["Exito"], evidence=evidencia, show_progress=False)
    estados = resultado.state_names["Exito"]
    idx_exito = estados.index(1) if 1 in estados else estados.index("1")
    return float(resultado.values[idx_exito])


def estimar_p_por_vacuna(
    modelo: DiscreteBayesianNetwork,
    caso: dict,
    vacunas_candidatas: list[str],
    config: Optional[ConfigDiscretizacion] = None,
) -> dict[str, float]:
    """Conveniencia: p_i para todas las vacunas candidatas, reusando la
    misma instancia de VariableElimination. Listo para pasarle directo a
    motor_probabilidades.esperanza_dosis(orden, p_por_vacuna)."""
    inferencia = VariableElimination(modelo)
    return {
        vacuna: estimar_probabilidad_bayesiana(modelo, caso, vacuna, config, inferencia)
        for vacuna in vacunas_candidatas
    }


# ---------------------------------------------------------------------------
# 6. Atajo + smoke test
# ---------------------------------------------------------------------------

def entrenar_desde_excel(
    path_excel: str,
    config: Optional[ConfigDiscretizacion] = None,
) -> tuple[DiscreteBayesianNetwork, EstructuraRed]:
    df_crudo = pd.read_excel(path_excel)
    df_prep, estructura = preparar_dataframe(df_crudo, config)
    modelo = entrenar_red(df_prep, estructura)
    return modelo, estructura


if __name__ == "__main__":
    PATH_EXCEL = "/mnt/user-data/uploads/historico_vacunas_SINTETICO.xlsx"

    config = ConfigDiscretizacion()
    modelo, estructura = entrenar_desde_excel(PATH_EXCEL, config)

    print("--- Esquema base (7 columnas actuales) ---")
    for nota in estructura.notas:
        print(" -", nota)
    print("Nodos:", modelo.nodes())
    print("\nCPD de Exito:")
    print(modelo.get_cpds("Exito"))

    df_crudo = pd.read_excel(PATH_EXCEL)
    vacunas_candidatas = sorted(df_crudo["vacuna"].unique())
    caso_ejemplo = {"edad": 30, "comorbilidad": 0}
    p_por_vacuna = estimar_p_por_vacuna(modelo, caso_ejemplo, vacunas_candidatas, config)

    print(f"\nCaso: {caso_ejemplo}")
    for vacuna, p in sorted(p_por_vacuna.items(), key=lambda x: -x[1]):
        print(f"  {vacuna:10s}  p={p:.3f}")

    # --- Segundo test: esquema extendido, con antecedentes granulares
    # sintéticos (NO viene del Excel real -- solo para validar que el
    # código detecta las columnas nuevas y arma Carga_comorbida bien) ---
    print("\n\n--- Esquema extendido (antecedentes granulares sintéticos) ---")
    import numpy as np
    rng = np.random.default_rng(7)
    df_ext = df_crudo.copy()
    n = len(df_ext)
    df_ext["diabetes"] = rng.integers(0, 2, n)
    df_ext["hipertension"] = rng.integers(0, 2, n)
    df_ext["acv_iam"] = rng.integers(0, 2, n)
    df_ext["alergias"] = rng.integers(0, 2, n)
    df_ext["tabaquismo"] = rng.integers(0, 2, n)
    df_ext["antecedentes_familiares"] = rng.integers(0, 2, n)
    df_ext["reaccion_adversa_previa"] = rng.integers(0, 2, n)
    df_ext["intervalo_semanas"] = rng.integers(2, 16, n)

    df_prep_ext, estructura_ext = preparar_dataframe(df_ext, config)
    for nota in estructura_ext.notas:
        print(" -", nota)
    modelo_ext = entrenar_red(df_prep_ext, estructura_ext)
    print("Nodos:", modelo_ext.nodes())

    caso_ext = {
        "edad": 30, "diabetes": 1, "hipertension": 0, "acv_iam": 0,
        "alergias": 1, "tabaquismo": 0, "antecedentes_familiares": 0,
        "reaccion_adversa_previa": 0, "intervalo_semanas": 6,
    }
    p_ext = estimar_p_por_vacuna(modelo_ext, caso_ext, vacunas_candidatas, config)
    print(f"\nCaso extendido: {caso_ext}")
    for vacuna, p in sorted(p_ext.items(), key=lambda x: -x[1]):
        print(f"  {vacuna:10s}  p={p:.3f}")
