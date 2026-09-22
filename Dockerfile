# Imagen base liviana con Python (fijamos "bookworm" explícitamente,
# porque el .deb de wkhtmltopdf de abajo está compilado para esa
# versión de Debian)
FROM python:3.12-slim-bookworm

# wkhtmltopdf ya no está en los repos de apt de Debian/Ubuntu (fue
# retirado de los paquetes oficiales) -- se instala bajando el .deb
# directo de las releases de su repo en GitHub. LibreOffice sigue
# viniendo de apt normal.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    xfonts-75dpi xfonts-base fontconfig \
    libjpeg62-turbo libxrender1 libxext6 libx11-6 \
    libreoffice --no-install-recommends \
    && wget -q -O /tmp/wkhtmltox.deb \
       https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb \
    && apt-get install -y /tmp/wkhtmltox.deb \
    && rm -f /tmp/wkhtmltox.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py motor_probabilidades.py generar_reporte.py ./

# El Excel histórico se monta como volumen o se sube vía
# /admin/actualizar-historico -- no se hornea en la imagen.
RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]