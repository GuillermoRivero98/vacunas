# Aplicar: tests automáticos del frontend (T6.4) — 2026-09-28

Instrucciones para Claude Code. Trabajá en la copia del repo que tenga Node.js instalado
(ver "Antes de empezar"). Aplicá los cambios con ediciones puntuales, no reescribas archivos enteros.

## Antes de empezar

1. Confirmá en qué carpeta estás y que es el repo correcto:
   `git -C <carpeta> log -1 --oneline` debe dar `ba30460 rtu nuevo` (o posterior) y `git status` limpio.
   Ojo: existen al menos dos rutas usadas, `C:\Users\guill\Desktop\vacunas` y
   `C:\Users\guill\OneDrive\Desktop\vacunas`. Decime cuál usaste y si son la misma carpeta o dos copias.
2. `node --version` debe responder (18 o posterior). Si Node no está instalado, pará y avisame:
   sin Node no se puede instalar Vitest ni actualizar `package-lock.json`.

## Cambios

1. **Copiar** `frontend/src/api.test.ts` y `frontend/src/formato.test.ts` de este zip a `frontend/src/` del repo
   (ruta completa; no extraer el zip dentro de `frontend/`).

2. **`frontend/src/api.ts`**, dos ediciones:
   - `function detallesDeError(detail: unknown): string[] {`
     → `export function detallesDeError(detail: unknown): string[] {`
   - `const BASE = (configurada ?? API_POR_DEFECTO).replace(/\/$/, "");`
     → `const BASE = (configurada || API_POR_DEFECTO).replace(/\/$/, "");`
     (bug: con `VITE_API_URL=` vacía la consola decía que usaba Render, pero pedía a rutas relativas).

3. **`frontend/tsconfig.json`**: agregar después de `"include": ["src"]` la clave
   `"exclude": ["src/**/*.test.ts"]`. Así un error en un test nunca rompe `tsc -b` ni el deploy de Pages.

4. **Instalar Vitest** desde `frontend/`:
   `npm install -D --save-exact vitest@2.1.9`
   (2.1.9 es la línea compatible con Vite 5.4.11: reutiliza el mismo Vite, no instala otro).
   Esto modifica `package.json` y `package-lock.json`: los dos van al commit.

5. **`frontend/package.json`**: en `"scripts"`, agregar `"test": "vitest run"` después de `"preview"`.

6. **README.md**:
   - Sección 16.4: `# 41 tests` → `# 46 tests`.
   - Sección 16.5, en el bloque de comandos, después de la línea de `npm run build`, agregar:
     `npm test                 # tests de la lógica pura (Vitest): api.ts y formato.ts`
     y debajo del bloque el párrafo:
     "Los tests (`frontend/src/*.test.ts`) quedan excluidos del `tsc -b` del build (`exclude` en `tsconfig.json`) y Cloudflare Pages no los corre: se corren a mano antes de cada push que toque el frontend. Los de `formato.ts` dependen del formato es-UY, que requiere un Node con ICU completo (las versiones oficiales lo traen)."
   - Tabla de la fase 6, fila T6.4, columna Estado: `[HECHO] (sin tests automáticos del frontend)`
     → `[HECHO] con tests automáticos (Vitest, 29 tests en frontend/src/*.test.ts)`.
   - Sección 17, agregar al final:
     `| 2026-09-28 | Tests automáticos del frontend con Vitest 2.1.9: 29 tests sobre api.ts (traducción de errores 422/400/503/500, 404, sin conexión, tiempo de espera, dirección de la API) y formato.ts. Corregido: con VITE_API_URL vacía se pedía a rutas relativas. Tests excluidos del build. |`

## Verificación (pegame las salidas, no un "listo")

Desde `frontend/`:
- `npm test` → debe terminar en `Tests  29 passed (29)`.
- `npm run build` → sin errores.
Desde la raíz:
- `python -m pytest -q` → `46 passed`.
- `(Get-Content README.md).Count`
- `git status` → deben aparecer exactamente: `README.md`, `frontend/package.json`, `frontend/package-lock.json`,
  `frontend/tsconfig.json`, `frontend/src/api.ts` (modificados) y `frontend/src/api.test.ts`,
  `frontend/src/formato.test.ts` (nuevos). Nada más.

Si todo coincide: commit desde la raíz con `git add -A`, mensaje "Frontend: tests con Vitest (T6.4) y fix de VITE_API_URL vacía", push,
y pegame `git log -1 --oneline`. Después confirmar en Cloudflare Pages → Deployments que el deploy de ese commit terminó bien.
