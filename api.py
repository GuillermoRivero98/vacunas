"""
API mínima que envuelve el motor de cálculo determinístico.

Endpoints:
  GET  /health                  -> chequeo de vida
  POST /calcular-orden          -> corre el pipeline completo y devuelve JSON
  POST /calcular-orden/reporte  -> igual, pero devuelve el PDF del reporte
  POST /calcular-orden-bayesiano -> Camino B (red bayesiana), mismo formato

El Excel histórico vive en Cloudflare R2 (ver almacenamiento_r2.py) --
Render (plan gratis/starter) no garantiza disco persistente entre
deploys, R2 sí. Nunca se manda texto clínico libre a ningún modelo de
lenguaje acá: todo el cálculo es determinístico, tal como definimos en
el diseño.
"""
import os
import shutil
import tempfile

from typing import Literal

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

import almacenamiento_r2
from motor_probabilidades import correr_pipeline, resultado_a_dict
from pipeline_bayesiano import correr_pipeline_bayesiano, resultado_bayesiano_a_dict
from generar_reporte import generar_html, convertir_a_pdf

app = FastAPI(title="Motor de orden de vacunación", version="0.1.0")

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
    return {"status": "ok", "excel_cargado": almacenamiento_r2.excel_disponible()}


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