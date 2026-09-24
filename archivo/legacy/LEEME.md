# Sistema legacy de "orden de vacunación" (archivado)

Retirado de producción el **2026-09-24** (ADR-19 del README principal). Nada de
esta carpeta se ejecuta, se importa ni entra en la imagen de Render. Se conserva
como referencia; el historial de git tiene además la versión exacta anterior.

## Qué hay

| Archivo | Qué era |
|---|---|
| `endpoints_legacy.py` | Los endpoints que tenía `api.py`: `/calcular-orden`, `/calcular-orden-bayesiano`, `/calcular-orden/reporte`, `/admin/actualizar-historico` |
| `motor_probabilidades.py` | Camino A legacy: posterior Beta sobre casos similares, chi-cuadrado, orden por p |
| `red_bayesiana.py`, `pipeline_bayesiano.py` | Camino B legacy: red bayesiana con pgmpy y bootstrap |
| `generar_reporte.py` | Reporte HTML/PDF con matplotlib, wkhtmltopdf y LibreOffice |
| `generar_datos_sinteticos.py`, `comparar_caminos.py` | Generador de datos y comparación de caminos del esquema de vacunas |
| `INSTRUCCIONES_DEPLOY.txt` | Notas de integración del Camino B legacy (mencionan el repo viejo `api-vacunas`) |
| `historico_vacunas_SIMULADO_presentacion.xlsx` | Excel de ejemplo del esquema legacy (si estaba en el repo) |

## Problemas conocidos que quedaron sin corregir

No se corrigieron porque el código salió de producción. Si se reactiva, corregirlos primero:

- **BUG-01** (`pipeline_bayesiano.py`, bootstrap): `isin(set(...))` elimina los pacientes repetidos del remuestreo, así que no es un bootstrap real; los intervalos salen ~25% más angostos de lo correcto.
- **BUG-02** (`motor_probabilidades.filtrar_similares`): con `vacunas_previas` usa también las filas de las vacunas ya probadas (todas fracasos), y esas vacunas aparecen como candidatas con p ≈ 0.
- **BUG-03** (`pipeline_bayesiano.py`, comparaciones pareadas): compara listas de muestras recortadas que pueden no estar alineadas si falló algún remuestreo.
- Los bloques `if __name__ == "__main__":` de `motor_probabilidades.py`, `generar_reporte.py` y `red_bayesiana.py` tienen rutas de entornos de otras sesiones (`/mnt/user-data/...`, `/home/claude/...`) que no existen en esta máquina.

## Cómo reactivarlo

1. Mover los `.py` de esta carpeta a la raíz del repo.
2. Pegar el contenido de `endpoints_legacy.py` en `api.py`, con sus imports.
3. En el `Dockerfile`, restaurar la instalación de LibreOffice y wkhtmltopdf y el `COPY` de estos módulos.
4. Volver a agregar `pgmpy==1.1.2` y `matplotlib==3.9.2` a `requirements.txt`.
5. Corregir los bugs de arriba.

El Excel legacy (`historico_vacunas.xlsx`) sigue en el bucket de R2; no molesta y se puede borrar desde el panel de Cloudflare si no se va a reactivar.
