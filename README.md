# API de orden de vacunación

API que calcula, para un paciente dado, el orden de aplicación de vacunas
que minimiza la cantidad esperada de dosis hasta cubrir todas — usando
matemática determinística y auditable (nada de texto libre a un LLM para
los números). Corre en Render como contenedor Docker, con el Excel
histórico almacenado en Cloudflare R2.

Deploy activo: `https://vacunas-mwyr.onrender.com`

## Estructura del proyecto

| Archivo | Rol |
|---|---|
| `api.py` | FastAPI: define los endpoints HTTP, valida el body con Pydantic, orquesta el resto de los módulos. Punto de entrada del servicio (`uvicorn api:app`). |
| `motor_probabilidades.py` | **Camino A**. Motor numérico determinístico: filtra "casos similares" en el histórico, calcula posterior Beta(k, n) por vacuna, IC95%, comparaciones pareadas P(A>B) y el orden óptimo (dosis esperadas mínimas, vía cadena de Markov absorbente). |
| `red_bayesiana.py` | **Camino B**. Estima la probabilidad de éxito de cada vacuna con una red bayesiana (`pgmpy`) en vez del filtro manual de "casos similares" del Camino A. Detecta solo si el Excel trae columnas extendidas (comorbilidades granulares, reacción adversa previa, intervalo entre dosis) y arma nodos compuestos si están. |
| `pipeline_bayesiano.py` | Envoltorio de producción del Camino B: mismo formato de salida que el Camino A (`resultado_bayesiano_a_dict`), IC95% y P(A>B) vía bootstrap por paciente, reutiliza `esperanza_dosis` de `motor_probabilidades.py` para el orden óptimo. |
| `almacenamiento_r2.py` | Sube/descarga el Excel histórico a Cloudflare R2 (API S3) en vez de disco local — Render no garantiza persistencia entre deploys/reinicios. |
| `generar_reporte.py` | Arma el reporte HTML (tabla + gráficos matplotlib embebidos en base64) a partir del resultado del motor, y lo convierte a PDF con `wkhtmltopdf`/LibreOffice (según disponibilidad). |
| `generar_datos_sinteticos.py` | Genera un Excel histórico simulado con señal real (no ruido puro), esquema base + extendido, para demos y para comparar Camino A vs B. |
| `comparar_caminos.py` | Herramienta de análisis (no se usa en producción): compara Camino A vs B para un caso puntual, y hace backtest con Brier score sobre todo el histórico, partición train/test por paciente. |
| `historico_vacunas_SIMULADO_presentacion.xlsx` | Excel de ejemplo usado para la demo — es el que se subió a R2 en las pruebas de este documento. |
| `Dockerfile` | Imagen de deploy: Python 3.12-slim + LibreOffice + wkhtmltopdf (para la generación de PDF) + dependencias de `requirements.txt`. |
| `requirements.txt` | Dependencias Python fijadas por versión. |
| `INSTRUCCIONES_DEPLOY.txt` | Notas de integración del Camino B al servicio (ya aplicadas). |

`comparar_caminos.py` y `generar_datos_sinteticos.py` son herramientas de
análisis/demo — no las importa `api.py` ni se copian a la imagen Docker.

## Arquitectura

```
Cliente (frontend / curl)
        │
        ▼
   api.py (FastAPI)
        │
        ├── almacenamiento_r2.py ──► Cloudflare R2 (Excel histórico)
        │
        ├── motor_probabilidades.py     (Camino A: posterior Beta)
        │
        ├── pipeline_bayesiano.py
        │        └── red_bayesiana.py   (Camino B: red bayesiana / pgmpy)
        │
        └── generar_reporte.py ──► wkhtmltopdf/LibreOffice (PDF)
```

Ambos caminos devuelven el mismo formato de JSON (mismas claves), para
que el frontend pueda mostrarlos de forma intercambiable. El Camino B
agrega `"metodo": "red_bayesiana"` como campo extra para distinguirse.

## Variables de entorno (configuradas en Render → Environment)

| Variable | Uso | Requerida |
|---|---|---|
| `ADMIN_API_KEY` | Protege `POST /admin/actualizar-historico` (header `X-API-Key`) | Sí — sin ella el endpoint admin queda inutilizable (falla explícito) |
| `R2_ACCOUNT_ID` | Cuenta de Cloudflare R2 | Sí, para que `excel_cargado` sea `true` |
| `R2_ACCESS_KEY_ID` | Access Key del token R2 | Sí |
| `R2_SECRET_ACCESS_KEY` | Secret Key del token R2 | Sí |
| `R2_BUCKET_NAME` | Bucket donde vive el Excel | No (default `vacunas-historico`) |
| `FRONTEND_ORIGINS` | Restringe CORS a orígenes específicos, separados por coma | No (default `*`) |

> Nota de seguridad: durante el debug de este deploy la `ADMIN_API_KEY`
> quedó expuesta en el historial de la conversación con el asistente.
> **Ya fue rotada** (2026-09-22) — el valor viejo no sirve más.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Chequeo de vida. Devuelve `{"status": "ok", "excel_cargado": bool}`. |
| `POST` | `/calcular-orden` | Camino A. Body: `CasoPaciente`. Devuelve JSON con orden sugerido, p_estimado/IC95% por vacuna, comparaciones pareadas y alerta de estratificación. |
| `POST` | `/calcular-orden-bayesiano` | Camino B, mismo body y mismo formato de salida (+ `metodo` y `notas_estructura`). |
| `POST` | `/calcular-orden/reporte` | Igual a `/calcular-orden`, pero devuelve el PDF del reporte (`application/pdf`). |
| `POST` | `/admin/actualizar-historico` | Reemplaza el Excel histórico en R2. Requiere header `X-API-Key`. Body: `multipart/form-data`, campo `archivo`. |

**Body `CasoPaciente`:**
```json
{
  "edad": 30,
  "comorbilidad": 0,
  "vacunas_previas": ["VacunaX"]   // opcional
}
```

## Cómo correrlo localmente

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn api:app --reload
```

Nota: `generar_reporte.py` necesita `wkhtmltopdf`/LibreOffice instalados
en el sistema para el endpoint `/calcular-orden/reporte` — en local puede
fallar si no los tenés; el resto de los endpoints no los necesita.

## Deploy

Render construye la imagen desde el `Dockerfile` en cada push a `main`
del repo conectado (`GuillermoRivero98/vacunas`). No hace falta correr
nada manualmente — el pipeline es:

```
git push origin main  →  Render clona el commit  →  docker build  →  deploy
```

## Casos de prueba ejecutados (deploy actual, 2026-09-22)

Todas las pruebas corrieron contra `https://vacunas-mwyr.onrender.com`
después de resolver tres bugs de deploy (Dockerfile no copiaba todos los
módulos, conflicto de versiones `numpy`/`pgmpy`, y un parámetro de
`boto3.Config` incompatible con la versión de `botocore` pineada).

### 1. Salud del servicio

| Caso | Resultado |
|---|---|
| `GET /health` (antes de subir Excel a R2) | `200` — `{"status":"ok","excel_cargado":false}` |
| `POST /admin/actualizar-historico` con API key válida + Excel | `200` — `{"status":"actualizado"}` |
| `GET /health` (después de subir Excel) | `200` — `{"status":"ok","excel_cargado":true}` |

### 2. Cálculo — casos válidos

| Caso | Resultado |
|---|---|
| `POST /calcular-orden` `{"edad":30,"comorbilidad":0}` | `200` — orden sugerido `[VacunaA, VacunaD, VacunaB, VacunaC]`, 253 casos similares, `dosis_esperadas_orden_optimo: 1.633` vs `peor_orden: 2.61` |
| `POST /calcular-orden-bayesiano` `{"edad":30,"comorbilidad":0}` | `200` — orden sugerido `[VacunaA, VacunaB, VacunaD, VacunaC]` (Camino B, 1400 casos usados por la red), `metodo: "red_bayesiana"` |
| `POST /calcular-orden/reporte` `{"edad":30,"comorbilidad":0}` | `200`, `Content-Type: application/pdf` — PDF válido verificado por firma de archivo, 30 páginas |

> Nota: Camino A y Camino B dan órdenes de vacunas ligeramente distintos
> para el mismo caso (`VacunaD`/`VacunaB` invertidos) — esperable, cada
> uno usa una estrategia de estimación distinta (posterior Beta sobre
> grupo similar vs. red bayesiana con más covariables). Sirve como
> ejemplo real para la demo de "comparación de caminos".

### 3. Validación y manejo de errores

| Caso | Resultado |
|---|---|
| `POST /calcular-orden` sin el campo `comorbilidad` | `422` — error de validación de Pydantic/FastAPI (`"Field required"`) |
| `POST /calcular-orden` con `comorbilidad: 5` (fuera de rango esperado 0/1) | `400` — `"No hay casos similares en el histórico con ese criterio de filtro"` (no rechaza el valor, simplemente no encuentra grupo similar) |
| `POST /calcular-orden` con `edad: -5` | `200` — **no valida edad negativa**, devuelve un resultado "normal" con 47 casos similares (ver Limitaciones conocidas) |
| `POST /admin/actualizar-historico` sin header `X-API-Key` | `401` — `"API key inválida o faltante"` |
| `POST /admin/actualizar-historico` con API key incorrecta | `401` — `"API key inválida o faltante"` |

## Limitaciones conocidas (no bloquean la demo)

- ~~`CasoPaciente` no valida rangos~~ — **corregido** (2026-09-22):
  `edad` ahora usa `Field(ge=0, le=120)` y `comorbilidad` usa
  `Literal[0, 1]` en `api.py`. Los dos casos de la tabla de arriba
  (`comorbilidad: 5`, `edad: -5`) ahora devuelven `422` de validación
  automática en lugar de `400`/`200` — falta re-desplegar y re-probar
  contra el servicio en vivo para confirmarlo (código cambiado
  localmente todavía no pusheado).

## Pendiente / no cubierto en esta ronda de pruebas

- CORS real desde el dominio del frontend (Cloudflare Pages) — solo se
  probó con `curl`, no desde navegador con origen cruzado.
- Latencia/cold start de Render en plan gratuito (el primer request tras
  inactividad puede tardar bastante más que los siguientes).
- Carga concurrente / múltiples requests simultáneos.
