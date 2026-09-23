"""
API mínima que envuelve el motor de cálculo determinístico.

Endpoints:
  GET  /health                  -> chequeo de vida
  POST /calcular-orden          -> corre el pipeline completo y devuelve JSON
  POST /calcular-orden/reporte  -> igual, pero devuelve el PDF del reporte
  POST /calcular-orden-bayesiano -> Camino B (red bayesiana), mismo formato

RTU (inyecciones intravítreas, ver README):
  POST /rtu/sugerir-plan                -> recomendación + casos similares
  POST /admin/rtu/actualizar-historico  -> sube, valida y activa el histórico RTU

El Excel histórico vive en Cloudflare R2 (ver almacenamiento_r2.py) --
Render (plan gratis/starter) no garantiza disco persistente entre
deploys, R2 sí. Nunca se manda texto clínico libre a ningún modelo de
lenguaje acá: todo el cálculo es determinístico, tal como definimos en
el diseño.
"""
import io
import os
import shutil
import tempfile

from typing import Literal

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, model_validator

import almacenamiento_r2
from motor_probabilidades import correr_pipeline, resultado_a_dict
from pipeline_bayesiano import correr_pipeline_bayesiano, resultado_bayesiano_a_dict
from generar_reporte import generar_html, convertir_a_pdf
from esquema_rtu import EsquemaInvalido, leer_y_validar
from servicio_rtu import (
    HistoricoNoDisponible,
    ServicioRTU,
    SolicitudInvalida,
    cargador_archivo,
    cargador_r2,
    datos_simulados_desde_entorno,
)

app = FastAPI(title="Motor de orden de vacunación / RTU", version="0.2.0")

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


class CasoPaciente(BaseModel):
    # ge/le acotan el rango de edad a algo clínicamente razonable;
    # Literal[0, 1] rechaza cualquier valor de comorbilidad que no sea
    # exactamente 0 o 1 -- antes ambos aceptaban cualquier entero
    # (incluida edad negativa) sin avisar, ver README, sección
    # "Limitaciones conocidas".
    edad: int = Field(ge=0, le=120)
    comorbilidad: Literal[0, 1]
    vacunas_previas: list[str] | None = None


@app.get("/health")
def health():
    # rtu_historico_cargado: si hay Excel RTU en R2 (o en el archivo
    # local si se usa RTU_HISTORICO_LOCAL). rtu_modelo_entrenado: si ya
    # se entrenó en memoria (ocurre en el primer /rtu/sugerir-plan).
    rtu_cargado = (os.path.exists(_path_local_rtu) if _path_local_rtu
                   else almacenamiento_r2.excel_disponible(almacenamiento_r2.R2_OBJECT_KEY_RTU))
    return {
        "status": "ok",
        "excel_cargado": almacenamiento_r2.excel_disponible(),
        "rtu_historico_cargado": rtu_cargado,
        **servicio_rtu.estado(),
    }


@app.post("/admin/actualizar-historico")
async def actualizar_historico(archivo: UploadFile = File(...), _=Depends(verificar_api_key)):
    """Reemplaza el Excel histórico en R2. Requiere el header X-API-Key
    con el valor configurado en ADMIN_API_KEY."""
    try:
        almacenamiento_r2.subir_historico(archivo.file)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"status": "actualizado"}


@app.post("/calcular-orden")
def calcular_orden(caso: CasoPaciente):
    if not almacenamiento_r2.excel_disponible():
        raise HTTPException(500, "No hay Excel histórico cargado todavía.")
    try:
        resultado = correr_pipeline(almacenamiento_r2.descargar_historico(), caso.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))

    return resultado_a_dict(resultado)


@app.post("/calcular-orden-bayesiano")
def calcular_orden_bayesiano(caso: CasoPaciente):
    if not almacenamiento_r2.excel_disponible():
        raise HTTPException(500, "No hay Excel histórico cargado todavía.")
    try:
        resultado = correr_pipeline_bayesiano(
            almacenamiento_r2.descargar_historico(), caso.model_dump()
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return resultado_bayesiano_a_dict(resultado)


@app.post("/calcular-orden/reporte")
def calcular_orden_reporte(caso: CasoPaciente):
    if not almacenamiento_r2.excel_disponible():
        raise HTTPException(500, "No hay Excel histórico cargado todavía.")
    try:
        resultado = correr_pipeline(almacenamiento_r2.descargar_historico(), caso.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))

    # No usamos "with tempfile.TemporaryDirectory()" porque borraría la
    # carpeta apenas termina esta función -- FileResponse recién lee el
    # archivo DESPUÉS, de forma asíncrona, y se rompía (bug real, se
    # encontró al probar este endpoint por primera vez de punta a punta).
    # En su lugar: carpeta temporal sin auto-borrado, y limpieza recién
    # después de que la respuesta terminó de enviarse (background task).
    tmp = tempfile.mkdtemp()
    path_html = f"{tmp}/reporte.html"
    generar_html(resultado, path_html)
    path_pdf = convertir_a_pdf(path_html)
    return FileResponse(
        path_pdf, media_type="application/pdf",
        filename="reporte_orden_vacunacion.pdf",
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )

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
    metodo: Literal["red_factorizada", "beta"] = "red_factorizada"
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
        almacenamiento_r2.subir_historico(io.BytesIO(contenido), almacenamiento_r2.R2_OBJECT_KEY_RTU)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    servicio_rtu.invalidar()
    return {"status": "actualizado", "pacientes": int(df["paciente_id"].nunique()),
            "visitas": len(df), "farmacos": sorted(df["farmaco"].astype(str).unique())}
