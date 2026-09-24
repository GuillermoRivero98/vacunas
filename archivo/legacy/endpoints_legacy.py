"""
ENDPOINTS LEGACY (vacunas) -- RETIRADOS de api.py el 2026-09-24 (ADR-19).

Este archivo NO se ejecuta ni se importa: guarda el código que servía los
endpoints del sistema anterior de "orden de vacunación", por si se quiere
retomar. Dependía de los módulos que están en esta misma carpeta
(motor_probabilidades.py, pipeline_bayesiano.py, red_bayesiana.py,
generar_reporte.py) y de LibreOffice/wkhtmltopdf para el PDF.

Endpoints que existían:
  POST /calcular-orden             Camino A legacy (Beta sobre casos similares)
  POST /calcular-orden-bayesiano   Camino B legacy (red bayesiana con pgmpy)
  POST /calcular-orden/reporte     PDF del Camino A
  POST /admin/actualizar-historico Carga del Excel legacy a R2 (X-API-Key)
  GET  /health -> campo "excel_cargado" (Excel legacy en R2)

Para reactivarlo: volver a mover los módulos de esta carpeta a la raíz,
pegar este código en api.py (con sus imports), restaurar en el Dockerfile
la instalación de LibreOffice y wkhtmltopdf y el COPY de los módulos, y
volver a agregar pgmpy y matplotlib a requirements.txt. El historial de
git conserva además la versión exacta anterior (commit previo a ADR-19).
"""
import shutil
import tempfile
from typing import Literal

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import almacenamiento_r2
from generar_reporte import convertir_a_pdf, generar_html
from motor_probabilidades import correr_pipeline, resultado_a_dict
from pipeline_bayesiano import correr_pipeline_bayesiano, resultado_bayesiano_a_dict

# En api.py estos endpoints estaban registrados sobre la misma `app` y usaban
# su verificar_api_key.
from api import app, verificar_api_key  # noqa: E402  (solo como referencia)


class CasoPaciente(BaseModel):
    # ge/le acotan el rango de edad a algo clínicamente razonable;
    # Literal[0, 1] rechaza cualquier valor de comorbilidad que no sea
    # exactamente 0 o 1 -- antes ambos aceptaban cualquier entero
    # (incluida edad negativa) sin avisar, ver README, sección
    # "Limitaciones conocidas".
    edad: int = Field(ge=0, le=120)
    comorbilidad: Literal[0, 1]
    vacunas_previas: list[str] | None = None



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

