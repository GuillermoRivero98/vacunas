# Imagen del backend RTU: solo Python y sus dependencias.
# (Hasta ADR-19 también instalaba LibreOffice y wkhtmltopdf para el
# reporte PDF del sistema legacy de vacunas; ya no hacen falta.)
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Solo los módulos que usa la API. Las herramientas offline
# (generar_datos_rtu.py, evaluar_rtu.py, fase4_rtu.py, markov demo) y
# tests/ no van a la imagen; archivo/ tampoco.
COPY api.py almacenamiento_r2.py ./
COPY supuestos_protocolo.py markov_rtu.py estimacion_rtu.py esquema_rtu.py explicacion_rtu.py servicio_rtu.py compras_rtu.py ./

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
