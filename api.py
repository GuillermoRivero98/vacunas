"""
API del sistema de apoyo a la decisión para tratamiento intravítreo (RTU).

Endpoints:
  GET  /health                          -> estado del servicio y del modelo
  POST /rtu/sugerir-plan                -> recomendación + casos similares
  POST /admin/rtu/actualizar-historico  -> sube, valida y activa el histórico RTU
  POST /rtu/estimacion-compra           -> demanda y compra sugerida por fármaco
  GET  /rtu/info                        -> fármacos, tamaño del histórico, supuestos (para la interfaz)

Los endpoints legacy de "orden de vacunación" (/calcular-orden y afines)
se retiraron el 2026-09-24 (ADR-19); su código está en archivo/legacy/.

El Excel histórico vive en Cloudflare R2 (ver almacenamiento_r2.py) --
Render (plan gratis/starter) no garantiza disco persistente entre
deploys, R2 sí. Nunca se manda texto clínico libre a ningún modelo de
lenguaje acá: todo el cálculo es determinístico, tal como definimos en
el diseño.
"""
import io
import os
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

import almacenamiento_r2
from esquema_rtu import EsquemaInvalido, leer_y_validar
from servicio_rtu import (
    HistoricoNoDisponible,
    ServicioRTU,
    SolicitudInvalida,
    cargador_archivo,
    cargador_r2,
    datos_simulados_desde_entorno,
    precalentar_desde_entorno,
)

app = FastAPI(title="Plan de tratamiento intravítreo (RTU)", version="0.3.0")

# Servicio RTU: por defecto lee el histórico desde R2. Para desarrollo
# local sin R2 se puede apuntar a un archivo con RTU_HISTORICO_LOCAL.
_path_local_rtu = os.environ.get("RTU_HISTORICO_LOCAL")
servicio_rtu = ServicioRTU(
    cargador_archivo(_path_local_rtu) if _path_local_rtu else cargador_r2(),
    datos_simulados=datos_simulados_desde_entorno(),
)


# Habilita que el frontend (Cloudflare Pages, otro origen) llame a esta
# API desde el navegador. "*" es deliberadamente permisivo: no hay
# cookies ni sesión de por medio (el único endpoint protegido usa un
# header X-API-Key explícito, que un origen no autorizado no puede
# adivinar), así que un origin list no agrega seguridad real acá y
# evita tener que hardcodear el dominio de Cloudflare Pages. Se puede
# restringir a FRONTEND_ORIGINS más adelante si hace falta.
FRONTEND_ORIGINS = os.environ.get("FRONTEND_ORIGINS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS.split(",") if FRONTEND_ORIGINS else ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# El API key se lee de una variable de entorno (nunca hardcodeado en
# el código). En Render se configura en el dashboard del servicio,
# pestaña "Environment". Si no está seteada, el endpoint de admin
# queda inutilizable por seguridad (falla explícito, no en silencio).
API_KEY = os.environ.get("ADMIN_API_KEY")


def verificar_api_key(x_api_key: str = Header(None)):
    if not API_KEY:
        raise HTTPException(500, "ADMIN_API_KEY no está configurada en el servidor.")
    if x_api_key != API_KEY:
        raise HTTPException(401, "API key inválida o faltante (header X-API-Key).")


@app.get("/health")
def health():
    # rtu_historico_cargado: si hay Excel RTU en R2 (o en el archivo
    # local si se usa RTU_HISTORICO_LOCAL). rtu_modelo_entrenado: si ya
    # se entrenó en memoria (ocurre en el primer /rtu/sugerir-plan).
    rtu_cargado = (os.path.exists(_path_local_rtu) if _path_local_rtu
                   else almacenamiento_r2.excel_disponible(almacenamiento_r2.R2_OBJECT_KEY_RTU))
    # Precalentamiento: si hay histórico y el modelo no está entrenado, se
    # entrena en segundo plano y /health responde en el acto. No se hace
    # al arrancar el servicio (ver servicio_rtu.precalentar_en_segundo_plano).
    # Render duerme el servicio tras un rato sin uso: llamar a /health antes
    # de usar el sistema lo despierta y lo deja entrenado.
    if rtu_cargado and precalentar_desde_entorno() and not servicio_rtu.estado()["rtu_modelo_entrenado"]:
        servicio_rtu.precalentar_en_segundo_plano()
    return {
        "status": "ok",
        "rtu_historico_cargado": rtu_cargado,
        **servicio_rtu.estado(),
    }


# ===========================================================================
# RTU -- inyecciones intravítreas (fase 5, ver README secciones 4 y 13)
# ===========================================================================

class CasoRTU(BaseModel):
    diagnostico: Literal["DMRE", "EMD", "OVCR", "ORVR"]
    tipo_mnv: Literal["MNV1", "MNV2", "MNV3"] | None = None
    edad: int = Field(ge=0, le=120)
    # Comorbilidades opcionales: si faltan, la red bayesiana marginaliza
    # esa variable (RF-06) y el k-NN no la usa para medir distancia.
    diabetes: Literal[0, 1] | None = None
    hipertension: Literal[0, 1] | None = None
    acv_iam_reciente: Literal[0, 1] | None = None
    tabaquismo: Literal[0, 1] | None = None
    farmacos_ya_probados: list[str] = Field(default_factory=list)
    objetivo: Literal["estable", "inyecciones"] = "estable"
    # Único método: el grafo probabilístico. "beta" (Camino A) está
    # desactivado (ADR-16); reactivarlo requiere agregarlo acá.
    metodo: Literal["red_factorizada"] = "red_factorizada"
    n_casos_similares: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def _coherencia_clinica(self):
        if self.tipo_mnv is not None and self.diagnostico != "DMRE":
            raise ValueError("tipo_mnv solo aplica a DMRE")
        if self.diagnostico == "EMD" and self.diabetes == 0:
            raise ValueError("El EMD implica diabetes: diabetes no puede ser 0")
        return self

    def caso(self) -> dict:
        c = {"diagnostico": self.diagnostico, "edad": self.edad}
        if self.tipo_mnv is not None:
            c["tipo_mnv"] = self.tipo_mnv
        for k in ("diabetes", "hipertension", "acv_iam_reciente", "tabaquismo"):
            v = getattr(self, k)
            if v is None and k == "diabetes" and self.diagnostico == "EMD":
                v = 1
            if v is not None:
                c[k] = v
        return c


@app.post("/rtu/sugerir-plan")
def rtu_sugerir_plan(solicitud: CasoRTU):
    try:
        return servicio_rtu.sugerir_plan(
            solicitud.caso(),
            farmacos_ya_probados=solicitud.farmacos_ya_probados,
            objetivo=solicitud.objetivo,
            metodo=solicitud.metodo,
            n_casos_similares=solicitud.n_casos_similares,
        )
    except HistoricoNoDisponible as e:
        raise HTTPException(503, str(e))
    except EsquemaInvalido as e:
        raise HTTPException(500, {"mensaje": "El histórico RTU cargado no es válido.", "errores": e.errores})
    except SolicitudInvalida as e:
        raise HTTPException(400, str(e))


@app.post("/admin/rtu/actualizar-historico")
async def rtu_actualizar_historico(archivo: UploadFile = File(...), _=Depends(verificar_api_key)):
    """Valida el Excel RTU ANTES de subirlo; si no cumple el esquema,
    no toca R2 y devuelve 400 con todos los problemas encontrados."""
    contenido = await archivo.read()
    try:
        df = leer_y_validar(io.BytesIO(contenido))
    except EsquemaInvalido as e:
        raise HTTPException(400, {"mensaje": "El archivo no cumple el esquema RTU.", "errores": e.errores})
    if _path_local_rtu:
        raise HTTPException(409, "El servicio usa RTU_HISTORICO_LOCAL; no se sube a R2 en ese modo.")
    try:
        # Primero el Excel original (para personas); al final la copia CSV,
        # que es la que lee el entrenamiento y cuyo ETag versiona el modelo.
        almacenamiento_r2.subir_historico(io.BytesIO(contenido), almacenamiento_r2.R2_OBJECT_KEY_RTU)
        csv = io.BytesIO(df.to_csv(index=False).encode("utf-8"))
        almacenamiento_r2.subir_historico(csv, almacenamiento_r2.R2_OBJECT_KEY_RTU_CSV)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    servicio_rtu.invalidar()
    servicio_rtu.precalentar_en_segundo_plano()
    return {"status": "actualizado", "pacientes": int(df["paciente_id"].nunique()),
            "visitas": len(df), "farmacos": sorted(df["farmaco"].astype(str).unique())}


class ParametrosCompra(BaseModel):
    horizonte_semanas: int = Field(default=52, ge=4, le=104)
    nivel_servicio: float = Field(default=0.95, ge=0.5, le=0.999)
    nuevos_ojos_por_semana: float = Field(default=0.0, ge=0.0, le=1000.0)


@app.post("/rtu/estimacion-compra")
def rtu_estimacion_compra(parametros: ParametrosCompra):
    """Demanda esperada de cada fármaco en el horizonte y compra sugerida
    para el nivel de servicio pedido (probabilidad de que alcance).
    La combinación por defecto se precalcula en segundo plano; otras
    combinaciones se calculan en el momento (puede tardar)."""
    try:
        return servicio_rtu.estimar_compra(**parametros.model_dump())
    except HistoricoNoDisponible as e:
        raise HTTPException(503, str(e))
    except EsquemaInvalido as e:
        raise HTTPException(500, {"mensaje": "El histórico RTU cargado no es válido.", "errores": e.errores})


@app.get("/rtu/info")
def rtu_info():
    """Para la interfaz: lista de fármacos, tamaño del histórico y supuestos.
    Responde en el acto; si el modelo no está listo devuelve listo=false."""
    return servicio_rtu.info()
