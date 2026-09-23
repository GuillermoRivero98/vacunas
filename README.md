# Sistema de apoyo a la decisión para tratamiento intravítreo (RTU)

> **Este documento es la fuente de verdad del proyecto.** Antes de asumir cualquier cosa sobre el sistema —en una sesión propia o con un asistente de IA— se consulta acá. Si algo no está en este documento, no se da por hecho: se verifica y se agrega.

Última actualización: 2026-09-23

---

## 0. Cómo usar este documento

### 0.1 Convenciones de estado

| Marca | Significado |
|---|---|
| **[HECHO]** | Implementado y verificado con evidencia (salida de ejecución, prueba contra el deploy). La evidencia está en la sección 12. |
| **[EN CURSO]** | Empezado, no terminado. |
| **[PENDIENTE]** | Definido, no empezado. |
| **[PROPUESTA]** | Diseño sugerido, todavía no aprobado. Puede cambiar. |
| **[SUPUESTO]** | Valor o regla elegida sin validación clínica ni datos reales. |
| **[A CONFIRMAR]** | Dato que no se conoce con certeza. **Nunca tratarlo como hecho.** |

### 0.2 Reglas para trabajar con asistentes de IA

1. Pasarle este README al inicio de cada sesión.
2. Ningún plazo, fecha de entrega o compromiso existe salvo que figure en la sección 15. (Antecedente: en una sesión anterior un asistente inventó una "demo de mañana" que no existía.)
3. Ninguna cifra de resultados se reporta si no está en la sección 12 o no se obtuvo corriendo el código.
4. Al terminar una sesión, actualizar la sección 16 (registro de cambios) y el estado de las tareas de la sección 13.
5. Distinguir siempre entre el servicio y repo actual (`vacunas`) y el anterior (`api-vacunas`). Ver sección 5.4.

---

## 1. Propósito y alcance

### 1.1 Qué es

Un sistema de **apoyo a la decisión clínica** para unidades de terapia retinal (RTU) que tratan con inyecciones intravítreas de fármacos anti-VEGF bajo el protocolo *treat-and-extend* (T&E). Dado un paciente, estima para cada fármaco candidato:

- cuántas inyecciones, visitas y semanas de tratamiento son esperables;
- la probabilidad de terminar en estabilidad, en cambio de fármaco (switch) o en abandono;

y le **recomienda al médico los fármacos con más chances de funcionar para ese paciente, mostrando en qué casos históricos parecidos se basa el cálculo**. Además sugiere en qué orden probarlos si hay que hacer switch.

### 1.2 Qué NO es

- No toma decisiones clínicas. La decisión final es siempre del médico tratante.
- No interpreta imágenes (OCT, retinografías). Eso queda como trabajo futuro.
- No usa un modelo de lenguaje para calcular nada (ver principio P1).
- **No está validado con datos reales ni por un clínico.** Hoy funciona sobre datos simulados y supuestos documentados.

### 1.3 Origen del proyecto

El sistema empezó como un "optimizador de orden de vacunación" (esquema *legacy*, sección 9.4). Se reencuadró al dominio de terapia intravítrea a partir de una presentación de una RTU de un hospital chileno y del software que usan (RetinApp), vistos en 22 fotografías. Las técnicas sugeridas por quien propuso el trabajo (grafo bayesiano, red neuronal, *machine learning*, leyes de probabilidad, cadenas de Markov) fueron **sugerencias, no requisitos**. Los requisitos explícitos fueron: incluir **cadenas de Markov** y usar **Cloudflare**.

> **Privacidad:** varias de esas fotografías muestran el nombre, RUT y edad de una paciente real en el encabezado de RetinApp. No se usan en el repo, el informe ni la presentación sin recortar o difuminar esa zona.

---

## 2. Glosario

| Término | Significado |
|---|---|
| RTU | *Retinal Therapy Unit*, unidad de inyecciones intraoculares. |
| Anti-VEGF | Familia de fármacos intravítreos (en las imágenes aparecen Avastin y Vabysmo/Faricimab). En el sistema se usan nombres genéricos: FarmacoA/B/C. |
| T&E | *Treat-and-extend*: si el ojo está seco se inyecta y se extiende el intervalo; si está activo, se inyecta y se acorta. |
| Fase de carga | Dosis iniciales a intervalo fijo antes del T&E. |
| Switch | Cambio de fármaco por falta de respuesta. |
| Línea | Posición del fármaco en la secuencia del ojo (1 = primer fármaco; 2+ = después de un switch). |
| Ciclo | Tramo del tratamiento de un ojo con un mismo fármaco, desde su carga hasta estable, switch, abandono o censura. |
| AV | Agudeza visual (decimal). |
| CMT | Grosor macular central, en µm (medido por OCT). |
| IRF / SRF | Líquido intrarretiniano / subretiniano (hallazgos de OCT que indican actividad). |
| OCT | Tomografía de coherencia óptica. |
| DMRE | Degeneración macular relacionada a la edad (exudativa). |
| EMD | Edema macular diabético. |
| OVCR / ORVR | Oclusión de vena central / de rama venosa retiniana. |
| MNV1/2/3 | Tipos de membrana neovascular (solo DMRE). |
| Verdad oculta | Parámetros reales con los que el generador simula a cada paciente. Solo existen en datos simulados. |
| Legacy | El esquema anterior de "vacunas" (sección 9.4). |

---

## 3. Principios de diseño (no negociables)

| Id | Principio | Consecuencia práctica |
|---|---|---|
| P1 | **Todo cálculo es determinístico y auditable.** Un LLM nunca calcula ni decide. | Mismo input → mismo output. Semillas fijas en todo lo aleatorio. Si alguna vez se agrega un LLM, solo narra un resultado ya calculado, sin inventar cifras, sin lenguaje causal clínico, mencionando limitaciones y aclarando que la decisión es del médico. |
| P2 | **La decisión final es del médico.** | Toda salida dirigida a usuarios lo aclara. |
| P3 | **Los supuestos del protocolo viven en un solo lugar** (`supuestos_protocolo.py`). | Generador y motor de Markov usan la misma regla. Cambiar un supuesto es cambiar un valor. |
| P4 | **La verdad oculta nunca la lee un estimador.** | Vive en un archivo aparte y solo la usan los scripts de evaluación. |
| P5 | **Partición train/test por paciente**, nunca por fila. | Los dos ojos y todas las visitas de un paciente quedan del mismo lado. |
| P6 | **Separar modelo de estimación.** | El motor de Markov recibe `p_activo(estado)` ya armada; no estima nada. |
| P7 | **Honestidad estadística.** | Se reportan incertidumbre, errores estándar y limitaciones. No se declara un ganador si las diferencias están dentro del ruido. |

---

## 4. Requerimientos

### 4.1 Funcionales

| Id | Requerimiento | Estado |
|---|---|---|
| RF-01 | Generar un histórico simulado por visita y por ojo con las variables de RetinApp. | [HECHO] |
| RF-02 | Exportar la verdad oculta por (paciente, ojo, fármaco), incluidos contrafácticos, en un archivo separado. | [HECHO] |
| RF-03 | Modelar el protocolo T&E como cadena de Markov absorbente y calcular visitas, inyecciones, semanas y probabilidades de absorción. | [HECHO] |
| RF-04 | Estimar `p_activo(estado, paciente, fármaco, línea)` con el Camino A (Beta). | [HECHO] |
| RF-05 | Estimar `p_activo` con el Camino B (red bayesiana, estructuras completa y factorizada). | [HECHO] |
| RF-06 | Si falta un dato del paciente, el Camino B marginaliza esa variable en vez de fallar. | [HECHO] |
| RF-07 | Recomendar fármacos y su orden. **Objetivo por defecto: maximizar P(estable)** (el fármaco con más chances de funcionar); alternativo: minimizar inyecciones esperadas. | [HECHO] |
| RF-08 | Recomendación consciente de línea (posición 1 con estimación de línea 1, siguientes con línea 2+). | [HECHO] |
| RF-09 | Testear la independencia entre fármacos (Cochran-Mantel-Haenszel). | [HECHO] |
| RF-10 | Evaluar estimadores y políticas contra la verdad oculta. | [HECHO] |
| RF-11 | Endpoint de API que devuelva la recomendación para un caso (`POST /rtu/sugerir-plan`). | [HECHO] en local; deploy pendiente (T5.9) |
| RF-12 | Endpoint protegido para cargar el histórico RTU en R2 y reentrenar (`POST /admin/rtu/actualizar-historico`). | [HECHO] en local; deploy pendiente (T5.9) |
| RF-13 | Validar el esquema del Excel RTU al subirlo y rechazar con mensaje claro si no cumple. | [HECHO] |
| RF-14 | Formulario y vista de resultado en el frontend para el caso RTU. | [PENDIENTE] Fase 6 |
| RF-15 | Excluir fármacos ya probados y calcular la línea a partir de ellos. | [HECHO] |
| RF-16 | Reporte PDF del resultado RTU. | [PENDIENTE] Opcional |
| RF-17 | Personalizar `p_activo` con la historia observada del propio ojo. | [PENDIENTE] Fase 8, opcional |
| RF-18 | Resumen en lenguaje natural por LLM con reglas de *grounding* (P1). | [PENDIENTE] Opcional, sin decidir |
| RF-19 | **Explicación por casos similares:** junto con cada recomendación, mostrar en qué casos históricos se basa: cuántos casos parecidos hubo por fármaco, cómo les fue (estable / switch / abandono, inyecciones) y un listado de los N casos más parecidos con sus características y su evolución. | [HECHO] |
| RF-20 | Los casos mostrados al médico se identifican solo con IDs anónimos del histórico, nunca con datos personales. | [HECHO] (el histórico tampoco debe contener datos personales) |

### 4.2 No funcionales

| Id | Requerimiento | Criterio | Estado |
|---|---|---|---|
| RNF-01 | Reproducibilidad | Mismas entradas y semillas → mismos resultados en cualquier máquina. | [HECHO] Verificado Linux vs Windows, fases 1 a 4. |
| RNF-02 | Auditabilidad | El Camino A informa k, n y nivel de similitud usado; todo cálculo es trazable. | [HECHO] |
| RNF-03 | Configurabilidad del protocolo | Cambiar un supuesto clínico = cambiar un valor en `supuestos_protocolo.py`. | [HECHO] |
| RNF-04 | Latencia de `/rtu/sugerir-plan` con el modelo entrenado | < 2 s | [HECHO] Render: 0.71 s medido desde Montevideo (incluye red). |
| RNF-05 | Arranque en frío en Render | El entrenamiento no debe recaer en la consulta del médico. | Medido: 88.8 s en la primera consulta (entrenando desde Excel). Mitigado con lectura CSV y precalentamiento al arrancar; [PENDIENTE] volver a medir tras el deploy. Antes de usar el sistema, llamar a `/health` y esperar `rtu_modelo_entrenado: true`. |
| RNF-06 | Memoria | La imagen con pgmpy y sus dependencias debe entrar en los recursos del plan de Render. | [A CONFIRMAR] qué recurso son los 10 GB informados (ver Q-05) y consumo real de RAM |
| RNF-07 | Seguridad | Endpoints de administración con `X-API-Key`; secretos solo en variables de entorno; el Excel nunca queda público. | [HECHO] en legacy; replicar en RTU |
| RNF-08 | Privacidad | Sin datos de pacientes reales en el repo ni en R2 hasta tener autorización formal. | Vigente |
| RNF-09 | Compatibilidad | Python 3.12; versiones fijadas en `requirements.txt`. | [HECHO] |
| RNF-10 | Mantenibilidad | Módulos RTU aditivos, sin romper los endpoints legacy mientras convivan. | Vigente |
| RNF-11 | Tiempo de evaluación offline | Poder correr con pocas réplicas mientras se desarrolla. | [PENDIENTE] parámetro `--replicas` |

---

## 5. Arquitectura e infraestructura

### 5.1 Componentes

```mermaid
flowchart LR
    U[Médico / navegador] -.->|Fase 6| FE[Frontend React<br/>Cloudflare Pages<br/>a construir]
    FE -.->|HTTPS JSON| API[API FastAPI<br/>Render, Docker]
    API --> R2[(Cloudflare R2<br/>Excel histórico)]
    API --> LEG[Módulos legacy<br/>vacunas]
    API -.->|Fase 5| RTU[Módulos RTU<br/>Markov + estimación]
    GH[GitHub<br/>GuillermoRivero98/vacunas] -->|push a main| API
```

### 5.2 Servicios

| Servicio | Uso | Detalle conocido | Estado |
|---|---|---|---|
| GitHub | Repositorio del backend | `GuillermoRivero98/vacunas`, rama `main`. Cada push dispara el deploy en Render. | [HECHO] |
| Render | Hosting del backend | Docker, plan gratuito, `https://vacunas-mwyr.onrender.com`. Límite informado en el panel: 10 GB (ver Q-05). | [HECHO] |
| Cloudflare R2 | Almacenamiento del Excel histórico | Bucket por defecto `vacunas-historico`. Objetos: `historico_vacunas.xlsx` (legacy), `historico_rtu.xlsx` (RTU, original) e `historico_rtu.csv` (RTU, copia que lee el entrenamiento; se genera al subir). Acceso autenticado vía API S3 (boto3). | [HECHO] |
| Cloudflare Pages | Hosting del frontend | **Todavía no hay frontend.** Se construye en la fase 6 (React, despliegue con `wrangler`). Hoy el requisito de usar Cloudflare lo cumple R2. | [PENDIENTE] |

### 5.3 Variables de entorno (Render → Environment)

| Variable | Uso | Requerida |
|---|---|---|
| `ADMIN_API_KEY` | Protege los endpoints de administración (header `X-API-Key`). Rotada el 2026-09-22 tras quedar expuesta en una conversación. | Sí |
| `R2_ACCOUNT_ID` | Cuenta de Cloudflare R2. | Sí |
| `R2_ACCESS_KEY_ID` | Access Key del token R2. | Sí |
| `R2_SECRET_ACCESS_KEY` | Secret Key del token R2. | Sí |
| `R2_BUCKET_NAME` | Bucket del Excel. | No (default `vacunas-historico`) |
| `FRONTEND_ORIGINS` | Restringe CORS a orígenes separados por coma. | No (default `*`) |
| `RTU_DATOS_SIMULADOS` | Si es `true`, la respuesta RTU advierte que los datos son simulados. Poner `false` recién con datos reales. | No (default `true`) |
| `RTU_PRECALENTAR` | Si es `true`, entrena el modelo RTU en segundo plano al arrancar el servicio. | No (default `true`) |
| `RTU_HISTORICO_LOCAL` | **Solo desarrollo:** ruta a un Excel RTU local en vez de R2. No configurarla en Render. | No |

### 5.4 Repos y servicios: cuál es cuál

| Nombre | Qué es | Estado |
|---|---|---|
| `GuillermoRivero98/vacunas` → `vacunas-mwyr.onrender.com` | **Repo y servicio actual.** Todo el desarrollo va acá. | Activo |
| `GuillermoRivero98/api-vacunas` → `api-vacunas.onrender.com` | **Primera versión, en desuso.** Se le portaron los fixes de R2 (commit `ec8e8a4`). No se desarrolla más ahí. | Obsoleto (ver T7.6) |
| Frontend | No existe todavía. Menciones anteriores a `api.ts`, `types.ts` o `ResultadoView.tsx` no corresponden a nada vigente. | [PENDIENTE] fase 6 |

---

## 6. Modelo del dominio y entidades

### 6.1 Diagrama entidad-relación (conceptual)

```mermaid
erDiagram
    PACIENTE ||--|{ OJO : "tiene 1 o 2 tratados"
    OJO ||--|{ CICLO : "recibe 1 por fármaco"
    FARMACO ||--o{ CICLO : "se usa en"
    CICLO ||--|{ VISITA : "contiene"
    OJO ||--|{ VERDAD_OCULTA : "1 por fármaco (solo simulado)"
    FARMACO ||--o{ VERDAD_OCULTA : ""
```

En los archivos Excel las entidades están **desnormalizadas** en una tabla plana (una fila por visita), por simplicidad y porque así lo consumen pandas y la API.

### 6.2 Entidades

| Entidad | Atributos | Notas |
|---|---|---|
| Paciente | `paciente_id`, `edad`, `diagnostico`, `diabetes`, `hipertension`, `acv_iam_reciente`, `tabaquismo`, `glaucoma`, `cristalino`, `tipo_mnv` | En el EMD, `diabetes` es siempre 1. `tipo_mnv` solo aplica a DMRE ("NA" en el resto). |
| Ojo | (`paciente_id`, `ojo`) con `ojo` ∈ {OD, OI} | Los dos ojos de un paciente no son independientes. |
| Fármaco | FarmacoA, FarmacoB, FarmacoC | Nombres genéricos a propósito: los efectos simulados son inventados. |
| Ciclo | Tramo de un ojo con un fármaco | Termina en `estable`, `switch`, `abandono` o `censurado`. |
| Visita | Ver sección 9.1 | Una fila por evaluación. En un switch hay dos filas en la misma semana: el cierre del fármaco viejo y la primera carga del nuevo. |
| EstadoCiclo | `fase`, `dosis_carga_previas`, `intervalo_semanas`, `activas_consecutivas_en_min`, `secas_consecutivas_en_max` | Estado markoviano; los contadores son necesarios para que la regla dependa solo del estado. |
| Decision | `accion`, `inyecta`, `siguiente`, `intervalo_hasta_proxima` | Salida de `transicion()`. |
| SupuestosProtocolo | Sección 8 | Inmutable (`frozen`). |
| ResultadoMarkov | `visitas_esperadas`, `inyecciones_esperadas`, `semanas_esperadas`, `prob_absorcion`, `visitas_por_estado` | Salida del motor para un fármaco. |
| Recomendacion | `por_farmaco`, `orden`, `valor_orden`, `objetivo` | Salida de `recomendar()` / `recomendar_por_linea()`. |

---

## 7. Modelo matemático

### 7.1 Cadena de Markov absorbente (un ojo, un fármaco)

**Estados transitorios** (se enumeran solos por BFS desde C1; con los supuestos actuales son 14):
`C1, C2, C3, M4, M4_a1, M4_a2, M6, M8, M10, M12, M14, M16, M16_s1, M16_s2`

- `C_k`: visita de carga k.
- `M_q`: mantenimiento con intervalo recién transcurrido de q semanas.
- `_a j`: j visitas activas consecutivas en el intervalo mínimo.
- `_s j`: j visitas secas consecutivas en el intervalo máximo.

**Estados absorbentes:** `switch`, `estable`, `abandono`.

**Una visita en el estado s:** con probabilidad `p_activo(s)` la enfermedad se evalúa activa; la regla del protocolo da la acción; si no es absorbente, se inyecta, se programa la próxima visita y antes de llegar el paciente abandona con probabilidad `prob_abandono_por_visita`.

**Cálculo:** con Q (transitorio → transitorio) y R (transitorio → absorbente), la matriz fundamental es `N = (I − Q)⁻¹`. Desde C1:

- visitas esperadas = Σ fila de N;
- inyecciones esperadas = fila de N · r_inyección;
- semanas esperadas = fila de N · r_semanas;
- probabilidades de absorción = fila de `N · R`.

### 7.2 Orden de fármacos: lema de intercambio generalizado

Si el fármaco i tiene costo esperado cᵢ (inyecciones) y probabilidad sᵢ de terminar en switch (única vía para pasar al siguiente):

```
E[inyecciones | orden] = c₁ + s₁·c₂ + s₁·s₂·c₃ + ...
```

El orden óptimo es **cᵢ / (1 − sᵢ) creciente** (intercambiando adyacentes: i antes que j si y solo si cᵢ(1 − sⱼ) ≤ cⱼ(1 − sᵢ)).

El modelo legacy de vacunas es el caso particular cᵢ = 1, sᵢ = 1 − pᵢ, que da "ordenar por pᵢ decreciente".

**Supuesto:** independencia entre fármacos. Los datos la rechazan (sección 12.4). La versión por línea usa parámetros distintos según la posición; ahí el lema no aplica y se busca exhaustivamente entre los k! órdenes (6 con 3 fármacos).

### 7.3 Estimación de `p_activo`

Discretizaciones comunes:

| Variable | Categorías |
|---|---|
| Tiempo | C1, C2, C3, q4, q6-8, q10-12, q14-16 |
| Subtipo | DMRE-MNV1, DMRE-MNV2, DMRE-MNV3, EMD, OVCR, ORVR |
| Línea | 1, 2+ |
| Edad (Camino B) | <65, 65-79, 80+ |
| Carga comórbida (Camino B) | Conteo de diabetes, hipertensión, ACV/IAM reciente y tabaquismo: 0, 1, 2+ |

**Camino A, Beta:** posterior `Beta(1 + k, 1 + n − k)` sobre visitas similares. Niveles, usando el primero con n ≥ 30:

1. subtipo + edad ± 10 + tiempo + fármaco + línea
2. subtipo + tiempo + fármaco + línea
3. tiempo + fármaco + línea
4. tiempo + fármaco

**Camino B, red bayesiana (pgmpy, prior Dirichlet uniforme):**

- *Completa:* Activo con 6 padres directos. Unas 2.300 combinaciones de padres; muchas celdas vacías.
- *Factorizada (recomendada):* `Activo ← Farmaco, Tiempo, Linea`; `Subtipo ← Activo, Farmaco`; `Edad ← Activo`; `Carga_comorbida ← Activo`. Unas 40 celdas en la tabla de Activo; efectos aproximadamente aditivos en log-odds.

Glaucoma y cristalino **no se usan** por decisión de diseño (ADR-07).

### 7.4 Evaluación

| Nivel | Métrica | ¿Posible con datos reales? |
|---|---|---|
| Visitas | Brier, log-loss | Sí |
| Elección de fármaco | Acierto top-1, Spearman y arrepentimiento contra la verdad oculta; techo = oráculo de covariables | No, solo simulado |
| Independencia | CMH con OR de Mantel-Haenszel, estratos tiempo × subtipo | Sí |
| Políticas | Inyecciones y P(estable) reales simulando cada orden con la verdad del ojo; oráculo con réplicas separadas | No, solo simulado |

---

## 8. Supuestos vigentes

**Todos son [SUPUESTO]:** inspirados en el esquema T&E típico de la literatura, sin validación clínica ni protocolo concreto. Las RTU usan protocolos NICE y Moorfields, con variantes por patología.

### 8.1 Protocolo (`supuestos_protocolo.py`)

| Parámetro | Valor |
|---|---|
| Dosis de carga | 3, cada 4 semanas |
| Si el ojo está seco | extender +2 semanas |
| Si el ojo está activo | acortar −2 semanas |
| Intervalo mínimo / máximo | 4 / 16 semanas |
| Switch | 3 visitas activas consecutivas en el intervalo mínimo |
| Estabilidad | 3 visitas secas consecutivas en el intervalo máximo |
| Abandono | 2% por visita programada |
| "Activo" | IRF o SRF presente, o CMT sube > 50 µm, o AV cae > 0.10 respecto de la visita previa |
| Fase de carga | La actividad no cambia la decisión |
| Día del switch | El nuevo fármaco arranca su carga en la misma visita |

### 8.2 Simulación (`generar_datos_rtu.py`)

| Parámetro | Valor |
|---|---|
| Pacientes / semilla | 1.500 / 2026 |
| Bilateralidad | 25% |
| Horizonte | 208 semanas (4 años) |
| Prevalencia | DMRE 50%, EMD 30%, OVCR 10%, ORVR 10% |
| Primer fármaco | A 65%, B 25%, C 10% |
| Fármaco al switchear | A 0.2, B 0.5, C 0.3 (entre los no probados) |
| Efectos latentes | Respondedor por paciente (sd 0.8), por ojo (sd 0.3), paciente × fármaco (sd 0.5) |
| Interacciones ocultas | EMD × FarmacoC, DMRE × FarmacoB |
| Covariables sin efecto a propósito | glaucoma, cristalino |
| Orden verdadero promedio | FarmacoB > FarmacoC > FarmacoA |

---

## 9. Datos

### 9.1 `historico_rtu_SIMULADO.xlsx` (una fila por evaluación)

| Columna | Tipo | Descripción |
|---|---|---|
| `paciente_id` | int | Identificador del paciente |
| `ojo` | OD / OI | Ojo tratado |
| `edad`, `diagnostico`, `diabetes`, `hipertension`, `acv_iam_reciente`, `tabaquismo`, `glaucoma`, `cristalino`, `tipo_mnv` | varios | Atributos del paciente (sección 6.2) |
| `visita_nro` | int | Número de visita del ojo |
| `semana` | int | Semana desde el inicio del tratamiento del ojo |
| `farmaco` | str | Fármaco de esa fila |
| `nro_farmaco_en_secuencia` | int | Línea (1, 2, 3) |
| `estado` | str | Etiqueta del estado markoviano (C1, M8, M4_a1, ...) |
| `fase` | carga / mantenimiento | |
| `intervalo_transcurrido_semanas` | int | 0 en la primera visita y en la carga post-switch |
| `av_decimal`, `cmt_um` | float | Observables |
| `irf`, `srf` | 0/1 | Observables |
| `activo` | 0/1 | Actividad según la regla del protocolo |
| `accion` | str | `inyectar_carga`, `inyectar_extender`, `inyectar_mantener`, `inyectar_acortar`, `switch`, `estable` |
| `inyectado` | 0/1 | |
| `nro_inyeccion_farmaco`, `nro_inyeccion_total` | int | Contadores acumulados |
| `intervalo_siguiente_semanas` | int / vacío | Vacío si la acción es absorbente |
| `desenlace_ciclo` | str / vacío | En la última fila del ciclo: `estable`, `switch`, `abandono`, `censurado` |

### 9.2 `verdad_oculta_rtu_SIMULADO.xlsx` (solo evaluación, P4)

| Columna | Descripción |
|---|---|
| `paciente_id`, `ojo`, `farmaco` | Clave |
| `p_activo_verdadera_q8` | P(activo latente) en mantenimiento a 8 semanas |
| `u_paciente`, `u_ojo`, `u_paciente_farmaco` | Efectos latentes |

### 9.3 Política de archivos de datos

- Los Excel **no se suben al repo**: se regeneran con `python generar_datos_rtu.py .` (semilla fija, resultado idéntico).
- La verdad oculta **nunca** va a R2 ni a producción.
- El histórico RTU **no reemplaza** al Excel legacy en R2: la API legacy espera otro esquema y se rompería.

### 9.4 Esquema legacy (vacunas)

Columnas mínimas que exige `motor_probabilidades.correr_pipeline`: `paciente_id, edad, comorbilidad, laboratorio, vacuna, nro_dosis_en_tratamiento, resultado`. Columnas extendidas opcionales que usa `red_bayesiana.py`: `diabetes, hipertension, acv_iam, alergias, tabaquismo, antecedentes_familiares, reaccion_adversa_previa, intervalo_semanas`.

---

## 10. Estructura del repositorio

### 10.1 Módulos RTU (nuevos, aditivos)

| Archivo | Rol | Usa | Estado |
|---|---|---|---|
| `supuestos_protocolo.py` | Supuestos, `EstadoCiclo`, `transicion()`, `evaluar_actividad()` | — | [HECHO] |
| `generar_datos_rtu.py` | Genera el histórico y la verdad oculta | supuestos | [HECHO] |
| `markov_rtu.py` | Cadena, matriz fundamental, orden óptimo, casos límite, Monte Carlo | supuestos | [HECHO] |
| `estimacion_rtu.py` | Caminos A y B, `recomendar()`, `recomendar_por_linea()` | markov, pgmpy | [HECHO] |
| `evaluar_rtu.py` | Evaluación offline fase 3 | estimación, generador | [HECHO] |
| `fase4_rtu.py` | Test CMH y evaluación de políticas | estimación, evaluar | [HECHO] |
| `esquema_rtu.py` | Validador del Excel RTU (T5.2) | — | [HECHO] |
| `explicacion_rtu.py` | Casos similares por k-NN y chequeo de discrepancias (T5.10, T5.11) | estimación | [HECHO] |
| `servicio_rtu.py` | Modelo en memoria con invalidación por ETag; arma la respuesta (T5.4, T5.5) | todos los anteriores, R2 | [HECHO] |
| `tests/` , `pytest.ini`, `requirements-dev.txt` | 28 tests automáticos (T5.8) | — | [HECHO] |

Entran en la imagen Docker: `supuestos_protocolo`, `markov_rtu`, `estimacion_rtu`, `esquema_rtu`, `explicacion_rtu` y `servicio_rtu`. Quedan afuera las herramientas offline (`generar_datos_rtu`, `evaluar_rtu`, `fase4_rtu`, `tests/`).

### 10.2 Módulos legacy (en producción)

| Archivo | Rol |
|---|---|
| `api.py` | FastAPI, endpoints legacy |
| `motor_probabilidades.py` | Camino A legacy (Beta, chi-cuadrado, orden por p) |
| `red_bayesiana.py`, `pipeline_bayesiano.py` | Camino B legacy |
| `almacenamiento_r2.py` | Subida y descarga del Excel en R2 |
| `generar_reporte.py` | Reporte HTML/PDF (wkhtmltopdf, LibreOffice) |
| `generar_datos_sinteticos.py`, `comparar_caminos.py` | Herramientas legacy de demo y análisis |
| `Dockerfile` | Python 3.12-slim + LibreOffice + wkhtmltopdf |
| `requirements.txt` | Dependencias fijadas |
| `INSTRUCCIONES_DEPLOY.txt` | Notas de integración del Camino B legacy (ya aplicadas; menciona `api-vacunas`, desactualizado) |

### 10.3 Endpoints legacy vigentes

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | `{"status": "ok", "excel_cargado": bool}` |
| POST | `/calcular-orden` | Camino A legacy. Body `{"edad", "comorbilidad", "vacunas_previas"?}` |
| POST | `/calcular-orden-bayesiano` | Camino B legacy, mismo body |
| POST | `/calcular-orden/reporte` | PDF del Camino A |
| POST | `/admin/actualizar-historico` | Reemplaza el Excel legacy en R2 (`X-API-Key`) |

### 10.4 Endpoints RTU

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/rtu/sugerir-plan` | Recomendación + casos similares (contrato en la fase 5, sección 13) |
| POST | `/admin/rtu/actualizar-historico` | Valida el Excel RTU y, si es válido, sube a R2 el original y una copia CSV, y reentrena en segundo plano (`X-API-Key`) |
| GET | `/health` | Suma `rtu_historico_cargado`, `rtu_modelo_entrenado`, `rtu_entrenando`, `rtu_version_modelo` y `rtu_ultimo_error` |

Códigos de error RTU: 422 entrada inválida (Pydantic o incoherencia clínica: `tipo_mnv` fuera de DMRE, EMD con `diabetes: 0`); 400 fármaco desconocido, todos ya probados o Excel inválido al subir (con la lista de errores); 401 sin API key; 503 si no hay histórico RTU cargado.

Pruebas legacy contra el deploy (2026-09-22): validación con Pydantic (edad 0 a 120, comorbilidad 0/1 → 422 si no cumple), 401 sin API key o con key incorrecta, PDF válido, `excel_cargado` pasa de false a true tras la carga.

---

## 11. Decisiones de diseño (ADR)

| Id | Decisión | Motivo | Alternativa descartada |
|---|---|---|---|
| ADR-01 | Reencuadrar de "vacunas" a terapia intravítrea | Las imágenes de la RTU muestran el caso real; da sentido a Markov y a las variables | Mantener vacunas genéricas |
| ADR-02 | Camino B como red bayesiana y no red neuronal | Probabilidades interpretables, marginaliza datos faltantes, pocos datos; las técnicas eran sugerencias | Red neuronal (queda para imágenes OCT, trabajo futuro) |
| ADR-03 | Estructura factorizada como Camino B por defecto | La completa rinde peor que no usar covariables (celdas vacías) | Red completa |
| ADR-04 | Estratificar por línea | Corrige el sesgo de selección (los de segunda línea responden peor) | Mezclar líneas |
| ADR-05 | Supuestos en un solo archivo | Garantiza que se simula y modela con las mismas reglas (P3) | Constantes repartidas |
| ADR-06 | Datos sintéticos con verdad oculta separada | Permite medir recuperación de la verdad, imposible con datos reales | Ir directo a datos reales |
| ADR-07 | No usar glaucoma ni cristalino | Clínicamente no se espera que modifiquen la respuesta anti-VEGF; revisable con un clínico | Incluir todas las variables |
| ADR-08 | Horizonte infinito en la cadena | La matriz fundamental lo resuelve en forma cerrada; se compara contra la verdad, no contra los conteos censurados | Horizonte finito |
| ADR-09 | Excel en R2 y no en disco de Render | Render no garantiza disco persistente entre deploys | Filesystem local |
| ADR-10 | CORS `*` por defecto | No hay cookies ni sesión; el endpoint sensible usa `X-API-Key` | Lista fija de orígenes (configurable con `FRONTEND_ORIGINS`) |
| ADR-11 | pgmpy fijado en 1.1.2 | `BayesianEstimator` se elimina en 1.3.0 | Actualizar sin migrar |
| ADR-12 | Evaluación de políticas con oráculo de réplicas separadas | Elegir y puntuar con la misma simulación infla el techo | Oráculo *in-sample* |
| ADR-13 | Objetivo por defecto: maximizar P(estable) | El médico quiere saber qué fármaco tiene más chances de funcionar; las inyecciones se muestran como dato secundario | Minimizar inyecciones por defecto |
| ADR-14 | Explicación por casos con vecinos más cercanos (k-NN) determinístico sobre las covariables | Auditable y reproducible (P1); le muestra al médico casos concretos, no solo un número. Aplica igual si la estimación viene del Camino B, que no tiene "casos" propios | Explicación generada por un LLM |

---

## 12. Resultados verificados (evidencia)

Todo lo de esta sección se obtuvo corriendo el código, en Linux y en Windows (venv con `requirements.txt`), con resultados idénticos.

### 12.1 Fase 1: datos simulados

34.268 filas, 1.500 pacientes, 1.856 ojos. Diagnósticos: DMRE 754, EMD 461, ORVR 147, OVCR 138. Inyecciones por ojo: media 18.2, mediana 19.

| Fármaco | abandono | censurado | estable | switch |
|---|---|---|---|---|
| FarmacoA | 0.328 | 0.400 | 0.138 | 0.133 |
| FarmacoB | 0.321 | 0.435 | 0.184 | 0.060 |
| FarmacoC | 0.351 | 0.373 | 0.129 | 0.147 |

Sesgo de selección (respondedor latente medio según hasta qué línea llega el ojo): línea 1: +0.106 (1.669 ojos), línea 2: −0.628 (148), línea 3: −1.277 (39).

### 12.2 Fase 2: motor de Markov

- 14 estados transitorios.
- Casos límite exactos: siempre seco → 12 visitas, 11 inyecciones, estable con probabilidad 1; siempre activo → 6 visitas, 5 inyecciones, switch con probabilidad 1.
- Analítico contra Monte Carlo (20.000 trayectorias): P(switch) 0.0424 vs 0.0441, P(estable) 0.2895 vs 0.2905, P(abandono) 0.6681 vs 0.6653.

### 12.3 Fase 3: estimación (train 1.050 pacientes / test 450)

| Camino | Brier | Acierto top-1 |
|---|---|---|
| Poblacional sin línea | 0.2358 | 0.535 |
| Poblacional con línea | 0.2340 | 0.535 |
| A Beta sin línea | 0.2346 | 0.569 |
| A Beta | 0.2335 | 0.563 |
| B red completa | 0.2425 | 0.478 |
| B red factorizada | **0.2326** | 0.503 |
| Techo (oráculo de covariables) | — | 0.601 |
| Azar | — | 0.357 |

Error estándar aproximado del acierto top-1: ±0.02 (561 ojos). Spearman del ranking poblacional con la verdad: 0.14 sin estratificar por línea contra 0.42 estratificando.

### 12.4 Fase 4: dependencia y políticas

| Fármaco | Activo línea 1 | Activo línea 2+ | OR_MH | p |
|---|---|---|---|---|
| FarmacoA | 0.420 | 0.634 | 2.28 | < 10⁻⁹ |
| FarmacoB | 0.362 | 0.494 | 1.71 | < 10⁻⁹ |
| FarmacoC | 0.378 | 0.547 | 1.88 | < 10⁻⁹ |

| Política | Inyecciones reales | P(estable) | 1er fármaco como el oráculo |
|---|---|---|---|
| Techo (oráculo) | 23.26 | 0.516 | 1.000 |
| B por línea | 25.26 | 0.473 | 0.553 |
| B independiente | 25.28 | 0.472 | 0.553 |
| Poblacional fijo (B → C → A) | 26.08 | 0.457 | 0.517 |
| A independiente | 26.72 | 0.444 | 0.460 |
| A por línea | 26.74 | 0.443 | 0.458 |
| Azar | 28.93 | 0.404 | 0.335 |

Duración de la simulación de la verdad: 81 s en Linux, 223 s en Windows.

### 12.5 Fase 5: API RTU (local)

- 28 tests automáticos pasan (`python -m pytest -q`; ~7 s en Linux, ~45 s en Windows por la importación de pgmpy).
- Con el histórico simulado completo: primera llamada 7.6 s (entrena), siguientes 0.04 s.
- Una copia con solo los archivos que copia el `Dockerfile` importa `api.py` sin errores.
- Tras cambiar el objetivo por defecto, `fase4_rtu.py` sigue dando exactamente los números de la sección 12.4 (usa `objetivo="inyecciones"` explícito).

### 12.6 Fase 5: API RTU en Render (2026-09-23)

- Carga del histórico: `{"status": "actualizado", "pacientes": 1500, "visitas": 34268}`.
- `/health` antes de la primera consulta: `rtu_historico_cargado: true`, `rtu_modelo_entrenado: false`.
- Primera consulta (entrena desde Excel): **88.8 s**. Siguientes: **0.71 s** (desde Montevideo, incluye red).
- Perfil del entrenamiento en local: leer Excel 4.97 s, red factorizada 4.07 s, índice de casos 1.53 s, Camino A 0.17 s, validación 0.03 s. Leer el mismo histórico en CSV: 0.06 s.
- Tras T5.13: entrenamiento completo local 6.6 s (Excel) → 1.8 s (CSV). [PENDIENTE] medir en Render.

### 12.7 Hallazgos para el informe

1. **Maldición de la dimensionalidad:** la red completa rinde peor que no usar covariables; la factorizada es la mejor prediciendo visitas.
2. **Sesgo de selección:** sin estratificar por línea, FarmacoC queda subestimado y el ranking se degrada.
3. **Significancia no es relevancia:** la dependencia entre fármacos es muy significativa, pero corregirla casi no cambia las decisiones porque pocos ojos llegan a la segunda posición (P(switch) ≈ 5-10%).
4. **Techo de personalización bajo:** solo con covariables se acierta el mejor fármaco en ~60% de los ojos; queda una brecha de ~2 inyecciones por ojo hasta el oráculo, atribuible a la respuesta individual del ojo.
5. **Estratificar a mano no escala:** el Camino A con covariables rinde peor que el orden poblacional en la evaluación de políticas.

---

## 13. Hoja de ruta

Orden recomendado: 5 → 6 → 7 → 8 (opcional) → 9. Ninguna fase tiene fecha comprometida.

### Fase 5: integración a la API [HECHO]

Estado: T5.1 a T5.12 **[HECHO]**. Desplegada en Render el 2026-09-23. Queda una sola verificación: volver a medir el arranque tras el deploy de las mejoras de T5.13 y T5.14, y anotar la memoria (Q-05).

**Objetivo:** que la recomendación RTU se pueda pedir por HTTP desde el deploy de Render.

| Id | Tarea | Criterio de aceptación |
|---|---|---|
| T5.1 | Parametrizar la clave del objeto en `almacenamiento_r2.py` (hoy fija en `historico_vacunas.xlsx`) para soportar un segundo objeto `historico_rtu.xlsx` | Legacy y RTU conviven en el mismo bucket sin pisarse |
| T5.2 | Validador del esquema RTU (columnas de la sección 9.1, tipos, valores permitidos) | Un Excel inválido devuelve 400 con la lista de problemas |
| T5.3 | `POST /admin/rtu/actualizar-historico` (`X-API-Key`) | Sube a R2, valida y reentrena; 401 sin key |
| T5.4 | Entrenamiento con caché en memoria | [PROPUESTA] entrenar al primer uso y tras cada carga; invalidar por ETag del objeto R2; nunca entrenar por request |
| T5.5 | `POST /rtu/sugerir-plan` (contrato abajo) | Devuelve el orden y las métricas por fármaco; 422 con entrada inválida |
| T5.6 | Extender `/health` con `rtu_historico_cargado` y `rtu_modelo_entrenado` | Visible tras el deploy |
| T5.7 | Agregar los módulos RTU al `Dockerfile` | El build en Render levanta sin errores de import |
| T5.8 | Tests automáticos (pytest): casos límite de Markov, validador, endpoint con datos simulados | Corren en local |
| T5.9 | Medir latencia en caliente, arranque en frío y memoria en Render | Números documentados en la sección 12 (RNF-04 a 06) |
| T5.10 | Módulo `explicacion_rtu.py`: k-NN determinístico sobre las covariables del paciente (distancia definida y documentada), resumen de desenlaces por fármaco entre los vecinos y listado de los N más parecidos | Mismo caso → mismos vecinos; ningún dato personal en la salida (RF-19, RF-20) |
| T5.11 | Evaluar si los desenlaces de los vecinos concuerdan con la estimación del modelo y advertir cuando discrepan mucho | Advertencia visible en la respuesta |
| T5.12 | Cambiar el objetivo por defecto a `estable` en `recomendar()` y `recomendar_por_linea()` (ADR-13) | Tests actualizados |
| T5.13 | Al subir el histórico, guardar también una copia CSV en R2 y entrenar desde ella | Entrenamiento local de 6.6 s a 1.8 s; misma recomendación desde CSV y Excel (test) |
| T5.14 | Entrenar en segundo plano al arrancar y después de cada carga | `/health` informa `rtu_entrenando`; test de precalentamiento |
| T5.15 | Mostrar como máximo un ojo por paciente en `casos_similares` | Test: ningún paciente repetido |

**Contrato de `POST /rtu/sugerir-plan`** [HECHO]:

```json
{
  "diagnostico": "DMRE",
  "tipo_mnv": "MNV2",
  "edad": 63,
  "diabetes": 1,
  "hipertension": 0,
  "acv_iam_reciente": 0,
  "tabaquismo": 0,
  "farmacos_ya_probados": [],
  "objetivo": "estable",
  "metodo": "red_factorizada",
  "n_casos_similares": 10
}
```

- `diagnostico`: DMRE, EMD, OVCR u ORVR. `tipo_mnv` solo con DMRE.
- Comorbilidades opcionales: si faltan, el Camino B las marginaliza (RF-06).
- `farmacos_ya_probados`: se excluyen de los candidatos y determinan la línea (RF-15).
- `objetivo`: `estable` (default) o `inyecciones`. `metodo`: `red_factorizada` (default) o `beta`.
- `n_casos_similares`: cuántos casos parecidos devolver en la explicación (RF-19).

Respuesta:

| Campo | Contenido |
|---|---|
| `orden_sugerido` | Fármacos ordenados según el objetivo |
| `valor_orden` | P(estable), inyecciones esperadas y P(agotar opciones) de la secuencia |
| `por_farmaco` | Para cada fármaco: P(estable), P(switch), P(abandono), inyecciones, visitas y semanas esperadas |
| `base_de_calculo` | Para cada fármaco: cuántos casos del histórico sustentan la estimación y con qué criterio de similitud (en el Camino A, el nivel usado y k/n) |
| `casos_similares` | Los N ojos más parecidos del histórico (ID anónimo, subtipo, edad, comorbilidades, distancia) con su evolución: fármacos recibidos, línea, desenlace, inyecciones |
| `supuestos` | Copia de los valores vigentes del protocolo |
| `advertencias` | Por ejemplo: "basado en datos simulados", "pocos casos similares para FarmacoC" |
| `version_modelo` | ETag del histórico, fecha de entrenamiento y tamaño (pacientes, ojos, visitas) |
| `metodo`, `objetivo`, `linea`, `farmacos_ya_probados` | Eco de la solicitud; `linea` = fármacos ya probados + 1 |
| `criterio_similitud` | Pesos de la distancia, K usado y línea considerada |

En `base_de_calculo`, con `metodo: beta` se agrega `camino_A_q6_8` (k, n y nivel de similitud del Camino A). Si hay pocos ciclos parecidos con un fármaco, o si la actividad observada en los casos similares difiere más de 0.15 de la estimada por el modelo, se agrega una advertencia.

### Fase 6: frontend [PENDIENTE]

| Id | Tarea | Criterio de aceptación |
|---|---|---|
**No hay frontend previo: se construye desde cero.**

| Id | Tarea | Criterio de aceptación |
|---|---|---|
| T6.0 | Crear el proyecto React (repo nuevo o carpeta en este, [A DECIDIR]) y el proyecto de Cloudflare Pages | Build y deploy de una página mínima |
| T6.1 | Formulario RTU con los campos del contrato | Valida antes de enviar |
| T6.2 | Vista de resultado: fármacos recomendados, P(estable) e inyecciones por fármaco, supuestos y aviso de que decide el médico | Legible en escritorio y celular |
| T6.3 | Vista de explicación: casos similares en los que se basó el cálculo y cómo evolucionaron (RF-19) | El médico puede ver los casos sin salir de la pantalla del resultado |
| T6.4 | Manejar el 422 de Pydantic (`detail` es un array de objetos, no un string) | Muestra el error sin romperse |
| T6.5 | Probar CORS real desde el dominio de Pages; restringir con `FRONTEND_ORIGINS` | Llamada exitosa desde el navegador |

### Fase 7: limpieza y deuda técnica [PENDIENTE]

| Id | Tarea |
|---|---|
| T7.1 | BUG-01 a BUG-03 (sección 14) |
| T7.2 | `.gitignore`: `venv/`, `.venv/`, `*.xlsx` generados, `__pycache__/` |
| T7.3 | Quitar rutas de sandbox de otras sesiones (`/mnt/user-data/...`, `/home/claude/...`) de los bloques `__main__` legacy |
| T7.4 | Parámetro `--replicas` e indicador de progreso en `fase4_rtu.py` (RNF-11) |
| T7.5 | Archivar `INSTRUCCIONES_DEPLOY.txt` (refiere a `api-vacunas`) |
| T7.6 | `api-vacunas` está en desuso: archivar el repo en GitHub y suspender o borrar su servicio en Render para evitar confusiones |
| T7.7 | Decidir cuándo se deprecan los endpoints legacy |

### Fase 8: personalización con la historia del ojo [PENDIENTE, opcional]

**Objetivo:** reducir la brecha de ~2 inyecciones hasta el oráculo usando las visitas ya observadas del propio ojo (por ejemplo, un posterior por ojo o un modelo jerárquico). Criterio de aceptación: mejora medible sobre "B por línea" en `fase4_rtu.py`, fuera del error de simulación.

### Fase 9: informe y presentación [PENDIENTE]

Escribir los hallazgos de la sección 12.7, las limitaciones (sección 14) y el trabajo futuro. Mostrar un ejemplo de recomendación con su explicación por casos similares. Incluir el mapeo "modelo de vacunas → caso RTU" y el lema generalizado (sección 7.2).

### Trabajo futuro (fuera de alcance)

- Validación clínica de los supuestos y obtención de datos reales, con las autorizaciones que correspondan.
- Red neuronal sobre imágenes OCT para detectar actividad (hay equipos de cuatro fabricantes, lo que complica generalizar).
- Modelo jerárquico bayesiano por subgrupos.
- Optimización de compras de fármacos con programación lineal.
- Más fármacos y protocolos diferenciados por patología.

---

## 14. Riesgos, limitaciones y deuda técnica

### 14.1 Bugs conocidos en código legacy

| Id | Ubicación | Problema | Impacto |
|---|---|---|---|
| BUG-01 | `pipeline_bayesiano.py`, bootstrap | `isin(set(...))` elimina los pacientes repetidos: no es un bootstrap real | Intervalos ~25% más angostos de lo correcto |
| BUG-02 | `motor_probabilidades.filtrar_similares` | Con `vacunas_previas` usa también las filas de las vacunas ya probadas (todas fracasos) | Las vacunas ya probadas aparecen como candidatas con p ≈ 0 |
| BUG-03 | `pipeline_bayesiano.py`, comparaciones pareadas | Compara listas de muestras recortadas que pueden no estar alineadas si falló algún remuestreo | Menor |

### 14.2 Limitaciones del modelo

- Supuestos clínicos no validados (sección 8).
- El abandono domina en horizonte infinito (~45%); el 2% por visita es un supuesto a revisar.
- Evaluación circular: los datos los generan supuestos propios; con datos reales solo quedan las métricas marcadas "Sí" en la sección 7.4.
- `p_activo` depende del intervalo pero no de los contadores del estado (simplificación).
- Los dos ojos de un paciente se modelan con un efecto compartido, pero la recomendación trata cada ojo por separado.

### 14.3 Riesgos operativos

- Arranque en frío del plan gratuito de Render.
- Recursos del plan de Render con pgmpy y sus dependencias (RNF-06, Q-05).
- Mostrar casos al médico: riesgo de reidentificación si en el futuro se usan datos reales con pocos casos por celda. Mitigación: solo IDs anónimos y no mostrar celdas con muy pocos casos.
- pgmpy 1.3 elimina `BayesianEstimator`: migrar antes de actualizar.
- Endpoints legacy y RTU conviviendo: riesgo de subir el Excel equivocado al objeto equivocado (mitigado por T5.1 y T5.2).

---

## 15. Preguntas abiertas y compromisos

| Id | Pregunta | Estado |
|---|---|---|
| Q-01 | Frontend | **Resuelta:** no existe todavía; se construye en la fase 6. |
| Q-02 | ¿Se usa `api-vacunas`? | **Resuelta:** fue la primera versión y no se usa (T7.6). |
| Q-03 | Objetivo de la recomendación | **Resuelta:** recomendar al médico los fármacos con más chances de funcionar y mostrar en qué casos similares se basó el cálculo (ADR-13, ADR-14, RF-19). |
| Q-04 | ¿Se incluye el resumen por LLM (RF-18)? | Sin decidir |
| Q-05 | Recursos del plan de Render | Informado: 10 GB. [A CONFIRMAR] a qué recurso corresponde (RAM, disco o ancho de banda): lo crítico para pgmpy es la RAM. |
| Q-06 | Contacto clínico para validar supuestos | Diferido hasta tener el sistema listo |
| Q-07 | ¿El frontend va en un repo aparte o en una carpeta de este? | [A DECIDIR] (T6.0) |
| Q-08 | Métrica de distancia para los casos similares | Implementada una [PROPUESTA] en `explicacion_rtu.PESOS_DISTANCIA`: subtipo (0 / 1 / 3), edad por década (1) y 0.5 por comorbilidad distinta. Revisar con un clínico. |

**Plazos comprometidos:** ninguno.

---

## 16. Cómo correr

### 16.1 Entorno (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Activar el entorno cada vez que se abre una terminal nueva; el prompt empieza con `(.venv)`. Si PowerShell bloquea el script de activación: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.

El `.gitignore` excluye `.venv/`, `venv/`, los Excel generados y los caches. Si un `git add` se corta y deja `.git\index.lock`, cortar los procesos con `Stop-Process -Name git -Force` antes de borrar el archivo.

### 16.2 Pipeline RTU

```powershell
python generar_datos_rtu.py .                       # fase 1: genera los dos Excel
python markov_rtu.py historico_rtu_SIMULADO.xlsx    # fase 2: tests + demo
python -W ignore evaluar_rtu.py                     # fase 3: ~20 s
python -W ignore fase4_rtu.py                       # fase 4: ~4 min en Windows
```

`-W ignore` oculta los `FutureWarning` de pgmpy, que no afectan con la versión 1.1.2.

### 16.3 API legacy en local

```powershell
uvicorn api:app --reload
```

`/calcular-orden/reporte` necesita wkhtmltopdf o LibreOffice instalados; el resto no.

### 16.4 Tests y API RTU en local

```powershell
pip install -r requirements-dev.txt
python -m pytest -q                                   # 28 tests
$env:RTU_HISTORICO_LOCAL = "historico_rtu_SIMULADO.xlsx"
uvicorn api:app --reload                              # abrir http://127.0.0.1:8000/docs
```

En `/docs` FastAPI muestra un formulario para probar `/rtu/sugerir-plan`. Para volver a usar R2 en local: `Remove-Item Env:RTU_HISTORICO_LOCAL`.

### 16.5 Deploy

Push a `main` de `GuillermoRivero98/vacunas` → Render clona → `docker build` → deploy. No hace falta ningún paso manual.

Para activar el RTU en Render (T5.9), una sola vez:

1. Push de todos los archivos nuevos y modificados. Esperar a que Render diga que el servicio está *live*.
2. Subir el histórico RTU:
   ```powershell
   curl.exe -X POST https://vacunas-mwyr.onrender.com/admin/rtu/actualizar-historico `
     -H "X-API-Key: TU_CLAVE" -F "archivo=@historico_rtu_SIMULADO.xlsx"
   ```
3. `GET /health` debe mostrar `rtu_historico_cargado: true`.
4. Esperar a que `/health` muestre `rtu_modelo_entrenado: true` (se entrena solo, en segundo plano). Medir una consulta a `/rtu/sugerir-plan` y mirar el uso de memoria en el panel de Render. Registrar todo en la sección 12.

Cada vez que se sube un histórico nuevo, el servicio reentrena solo en segundo plano; mientras tanto, `/health` muestra `rtu_entrenando: true`.

---

## 17. Registro de cambios

| Fecha | Cambio |
|---|---|
| 2026-09-22 | Deploy legacy verificado en `vacunas-mwyr.onrender.com`; validación de entrada con Pydantic; rotación de `ADMIN_API_KEY`; fixes de R2 portados a `api-vacunas`. |
| 2026-09-22 | Reencuadre a RTU. Fases 1 a 4 implementadas y verificadas en Linux y Windows. |
| 2026-09-22 | Este README reemplaza al anterior como fuente de verdad única. |
| 2026-09-22 | Resueltas Q-01 a Q-03: no hay frontend todavía; `api-vacunas` en desuso; objetivo = fármacos con más chances de funcionar + explicación por casos similares (RF-19, RF-20, ADR-13, ADR-14, T5.10 a T5.12, T6.0 y T6.3). |
| 2026-09-23 | Fase 5 en local: endpoints RTU, validador, casos similares, servicio con caché por ETag, objetivo por defecto `estable`, 25 tests, `Dockerfile` actualizado. Pendiente T5.9 (deploy y mediciones). |
| 2026-09-23 | Fase 5 desplegada en Render: histórico RTU en R2, 0.71 s por consulta, 88.8 s la primera. Mejoras T5.13 (copia CSV) y T5.14 (precalentamiento) y T5.15 (un ojo por paciente); `.gitignore` agregado. |
