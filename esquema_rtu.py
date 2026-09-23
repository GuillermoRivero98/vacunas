"""
esquema_rtu.py  --  Fase 5 (T5.2)

Valida que un Excel histórico RTU tenga el esquema que esperan los
estimadores y el módulo de explicación, ANTES de subirlo a R2. Devuelve
la lista completa de problemas (no solo el primero), para que quien
sube el archivo pueda corregir todo de una vez.

El esquema está documentado en el README, sección 9.1.
"""
from __future__ import annotations

import pandas as pd

DIAGNOSTICOS = {"DMRE", "EMD", "OVCR", "ORVR"}
TIPOS_MNV = {"MNV1", "MNV2", "MNV3", "NA"}
OJOS = {"OD", "OI"}
FASES = {"carga", "mantenimiento"}
DESENLACES = {"", "estable", "switch", "abandono", "censurado"}
BINARIAS = ["diabetes", "hipertension", "acv_iam_reciente", "tabaquismo", "activo", "inyectado"]

COLUMNAS_REQUERIDAS = [
    "paciente_id", "ojo", "edad", "diagnostico", "tipo_mnv",
    "diabetes", "hipertension", "acv_iam_reciente", "tabaquismo",
    "visita_nro", "semana", "farmaco", "nro_farmaco_en_secuencia",
    "estado", "fase", "intervalo_transcurrido_semanas",
    "activo", "inyectado", "nro_inyeccion_total", "desenlace_ciclo",
]
# Columnas que el generador produce pero el sistema no necesita:
# se aceptan si están, no se exigen.
COLUMNAS_OPCIONALES = [
    "glaucoma", "cristalino", "av_decimal", "cmt_um", "irf", "srf",
    "accion", "nro_inyeccion_farmaco", "intervalo_siguiente_semanas",
]

MAX_ERRORES = 25
MIN_PACIENTES = 30


class EsquemaInvalido(ValueError):
    def __init__(self, errores: list[str]):
        self.errores = errores
        super().__init__("; ".join(errores))


def _ejemplos(serie: pd.Series, n: int = 3) -> str:
    return ", ".join(repr(v) for v in serie.unique()[:n])


def validar(df: pd.DataFrame) -> list[str]:
    """Lista de problemas encontrados; vacía si el esquema es válido."""
    errores: list[str] = []

    faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    if faltantes:
        return [f"Faltan columnas requeridas: {', '.join(faltantes)}"]
    if df.empty:
        return ["El archivo no tiene filas."]

    def chequear(mascara_malos: pd.Series, columna: str, regla: str):
        if mascara_malos.any():
            errores.append(f"'{columna}': {int(mascara_malos.sum())} filas {regla} "
                           f"(ej.: {_ejemplos(df.loc[mascara_malos, columna])})")

    chequear(~df["ojo"].isin(OJOS), "ojo", f"fuera de {sorted(OJOS)}")
    chequear(~df["diagnostico"].isin(DIAGNOSTICOS), "diagnostico", f"fuera de {sorted(DIAGNOSTICOS)}")
    tipo = df["tipo_mnv"].fillna("NA").astype(str)
    chequear(~tipo.isin(TIPOS_MNV), "tipo_mnv", f"fuera de {sorted(TIPOS_MNV)}")
    chequear((df["diagnostico"] != "DMRE") & (tipo != "NA"), "tipo_mnv",
             "con valor distinto de NA en un diagnóstico que no es DMRE")
    chequear(~df["fase"].isin(FASES), "fase", f"fuera de {sorted(FASES)}")
    chequear(~df["desenlace_ciclo"].fillna("").astype(str).isin(DESENLACES), "desenlace_ciclo",
             f"fuera de {sorted(DESENLACES - {''})} o vacío")

    for col in BINARIAS:
        chequear(~df[col].isin([0, 1]), col, "que no son 0 ni 1")

    edad = pd.to_numeric(df["edad"], errors="coerce")
    chequear(edad.isna() | (edad < 0) | (edad > 120), "edad", "no numéricas o fuera de 0-120")
    linea = pd.to_numeric(df["nro_farmaco_en_secuencia"], errors="coerce")
    chequear(linea.isna() | (linea < 1), "nro_farmaco_en_secuencia", "no numéricas o menores a 1")
    intervalo = pd.to_numeric(df["intervalo_transcurrido_semanas"], errors="coerce")
    chequear(intervalo.isna() | (intervalo < 0), "intervalo_transcurrido_semanas", "no numéricas o negativas")
    chequear(~df["estado"].astype(str).str.match(r"^(C\d+|M\d+(_a\d+)?(_s\d+)?)$"), "estado",
             "con formato inválido (se espera C1, M8, M4_a1, M16_s2, ...)")
    chequear(df["farmaco"].isna() | (df["farmaco"].astype(str).str.strip() == ""), "farmaco", "vacías")

    chequear((df["diagnostico"] == "EMD") & (df["diabetes"] != 1), "diabetes",
             "con EMD pero sin diabetes (el EMD implica diabetes)")

    n_pac = df["paciente_id"].nunique()
    if n_pac < MIN_PACIENTES:
        errores.append(f"Solo hay {n_pac} pacientes; se requieren al menos {MIN_PACIENTES} "
                       f"para estimar algo con sentido.")

    return errores[:MAX_ERRORES]


def leer_y_validar(fuente, formato: str = "xlsx") -> pd.DataFrame:
    """Lee el histórico (path o file-like) en formato "xlsx" o "csv" y
    lanza EsquemaInvalido con todos los problemas si no cumple el esquema."""
    try:
        df = pd.read_csv(fuente) if formato == "csv" else pd.read_excel(fuente)
    except Exception as e:  # archivo corrupto, formato equivocado, etc.
        raise EsquemaInvalido([f"No se pudo leer el archivo como {formato}: {e}"]) from e
    errores = validar(df)
    if errores:
        raise EsquemaInvalido(errores)
    df["desenlace_ciclo"] = df["desenlace_ciclo"].fillna("").astype(str)
    df["tipo_mnv"] = df["tipo_mnv"].fillna("NA").astype(str)
    return df
