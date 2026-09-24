import type { Compra, Info, Plan, Salud, SolicitudCompra, SolicitudPlan } from "./tipos";

// Dirección de la API: sale de frontend/.env.production (o .env.development).
// Si ese archivo no llegó a la compilación, se usa la de Render y se avisa en
// la consola, en lugar de dejar la página en blanco.
const API_POR_DEFECTO = "https://vacunas-mwyr.onrender.com";
const configurada = import.meta.env.VITE_API_URL as string | undefined;
if (!configurada) {
  console.warn(`VITE_API_URL no está definida en la compilación; se usa ${API_POR_DEFECTO}.`);
}
const BASE = (configurada ?? API_POR_DEFECTO).replace(/\/$/, "");

export class ErrorApi extends Error {
  constructor(public estado: number, public detalles: string[], public ruta = "") {
    super(detalles.join(" "));
  }
}

// Traducción de los campos del formulario para los mensajes de validación.
const CAMPOS: Record<string, string> = {
  edad: "Edad",
  diagnostico: "Diagnóstico",
  tipo_mnv: "Tipo de membrana",
  diabetes: "Diabetes",
  horizonte_semanas: "Horizonte",
  nivel_servicio: "Nivel de servicio",
  nuevos_ojos_por_semana: "Ojos nuevos por semana",
};

/** Convierte cualquier forma de `detail` de la API en frases legibles:
 *  - 422 de Pydantic: lista de {loc, msg}
 *  - errores del Excel: {mensaje, errores: [...]}
 *  - resto: un texto */
function detallesDeError(detail: unknown): string[] {
  if (Array.isArray(detail)) {
    return detail.map((d: { loc?: (string | number)[]; msg?: string }) => {
      const campo = d.loc?.filter((x) => x !== "body").at(-1);
      const nombre = typeof campo === "string" ? CAMPOS[campo] ?? campo : null;
      const msg = (d.msg ?? "Valor inválido").replace(/^Value error, /, "");
      return nombre ? `${nombre}: ${msg}` : msg;
    });
  }
  if (detail && typeof detail === "object" && "mensaje" in detail) {
    const d = detail as { mensaje: string; errores?: string[] };
    return [d.mensaje, ...(d.errores ?? [])];
  }
  return [typeof detail === "string" ? detail : "La API respondió con un error inesperado."];
}

async function pedir<T>(ruta: string, opciones: RequestInit = {}, timeoutMs = 90_000): Promise<T> {
  const control = new AbortController();
  const reloj = setTimeout(() => control.abort(), timeoutMs);
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${ruta}`, {
      ...opciones,
      signal: control.signal,
      headers: { "Content-Type": "application/json", ...(opciones.headers ?? {}) },
    });
  } catch (e) {
    const expiro = e instanceof DOMException && e.name === "AbortError";
    throw new ErrorApi(0, [
      expiro
        ? "El servidor tardó demasiado en responder. Si estaba dormido, esperá un minuto y probá de nuevo."
        : `No se pudo conectar con la API en ${BASE}. Verificá que esté en funcionamiento y que haya conexión.`,
    ]);
  } finally {
    clearTimeout(reloj);
  }
  if (!resp.ok) {
    let cuerpo: { detail?: unknown } = {};
    try {
      cuerpo = await resp.json();
    } catch {
      /* respuesta sin JSON */
    }
    // La API nunca devuelve 404 a propósito (sin histórico responde 503):
    // un 404 significa que el servidor no tiene esa ruta.
    if (resp.status === 404) {
      throw new ErrorApi(404, [
        `La API en ${BASE} respondió, pero no tiene ${ruta}. Probablemente el servidor tiene una versión anterior: revisá que el último deploy de Render haya terminado bien.`,
      ], ruta);
    }
    throw new ErrorApi(resp.status, detallesDeError(cuerpo.detail), ruta);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  salud: () => pedir<Salud>("/health", {}, 120_000),
  info: () => pedir<Info>("/rtu/info"),
  plan: (s: SolicitudPlan) => pedir<Plan>("/rtu/sugerir-plan", { method: "POST", body: JSON.stringify(s) }),
  compra: (s: SolicitudCompra) =>
    pedir<Compra>("/rtu/estimacion-compra", { method: "POST", body: JSON.stringify(s) }, 300_000),
};
