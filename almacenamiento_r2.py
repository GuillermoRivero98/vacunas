"""
Storage del Excel histórico en Cloudflare R2 (compatible con la API S3),
en reemplazo del filesystem local -- Render (plan gratis/starter) no
garantiza disco persistente entre deploys/reinicios, R2 sí.

Requiere las siguientes variables de entorno (mismo criterio que
ADMIN_API_KEY en api.py: nunca hardcodeadas, se configuran en el
dashboard de Render, pestaña "Environment"):

  R2_ACCOUNT_ID          -- ID de cuenta de Cloudflare
  R2_ACCESS_KEY_ID        -- Access Key ID del token de R2
  R2_SECRET_ACCESS_KEY     -- Secret Access Key del token de R2
  R2_BUCKET_NAME (opcional, default "vacunas-historico")

Cómo generar el token: Cloudflare dashboard -> R2 -> Manage R2 API
Tokens -> Create API Token (permisos "Object Read & Write", scopeado
al bucket si se puede). El "Account ID" está en el dashboard de R2,
columna derecha.

El bucket y el objeto en sí NO requieren configuración de acceso
público -- esta API accede vía API S3 autenticada, server-to-server;
el Excel nunca queda expuesto por una URL pública.
"""
from __future__ import annotations

import io
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "vacunas-historico")
R2_OBJECT_KEY = "historico_vacunas.xlsx"


def _variables_faltantes() -> list[str]:
    return [
        nombre
        for nombre, valor in [
            ("R2_ACCOUNT_ID", R2_ACCOUNT_ID),
            ("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID),
            ("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY),
        ]
        if not valor
    ]


def _cliente_r2():
    faltantes = _variables_faltantes()
    if faltantes:
        raise RuntimeError(
            f"Variables de entorno de R2 sin configurar: {', '.join(faltantes)}"
        )
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        # NOTA: antes acá había request_checksum_calculation="when_required"
        # en el Config, pensado para evitar que boto3 (>=1.36) mande por
        # defecto un checksum CRC32 que R2 podía rechazar. Ese parámetro
        # NO EXISTE en botocore <1.36 -- y como requirements.txt fija
        # boto3==1.35.36, botocore SIEMPRE resuelve por debajo de 1.36
        # (restricción propia de esa versión de boto3), así que el
        # parámetro nunca puede estar disponible. Con esa combinación de
        # versiones, Config(...) tiraba TypeError en cada llamada -- 500
        # en /health y en cualquier endpoint que tocara R2. Se saca acá;
        # si en algún momento se actualiza boto3 a >=1.36, evaluar si
        # hace falta volver a agregarlo (R2 no debería necesitarlo).
        config=Config(signature_version="s3v4"),
    )


def excel_disponible() -> bool:
    """True si hay un Excel histórico cargado en R2. No lanza excepción
    si las variables de entorno de R2 no están configuradas -- devuelve
    False (mismo comportamiento que "no existe el archivo" de antes)."""
    if _variables_faltantes():
        return False
    try:
        _cliente_r2().head_object(Bucket=R2_BUCKET_NAME, Key=R2_OBJECT_KEY)
        return True
    except ClientError:
        return False


def descargar_historico() -> io.BytesIO:
    """Descarga el Excel histórico desde R2 a memoria y devuelve un
    BytesIO listo para pd.read_excel(...). Se descarga fresco en cada
    llamada (igual que antes se leía fresco del disco en cada request
    -- sin cache para no arriesgar servir una versión vieja)."""
    buf = io.BytesIO()
    _cliente_r2().download_fileobj(R2_BUCKET_NAME, R2_OBJECT_KEY, buf)
    buf.seek(0)
    return buf


def subir_historico(archivo) -> None:
    """Sube (reemplaza) el Excel histórico en R2. `archivo` es un
    file-like object (p.ej. UploadFile.file de FastAPI)."""
    _cliente_r2().upload_fileobj(archivo, R2_BUCKET_NAME, R2_OBJECT_KEY)