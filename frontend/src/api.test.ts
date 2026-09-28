import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SolicitudCompra, SolicitudPlan } from "./tipos";

const SOLICITUD: SolicitudPlan = {
  diagnostico: "EMD", edad: 70, farmacos_ya_probados: [], objetivo: "estable", n_casos_similares: 5,
};
const COMPRA: SolicitudCompra = { horizonte_semanas: 52, nivel_servicio: 0.95, nuevos_ojos_por_semana: 0 };

// api.ts calcula la dirección de la API al importarse, a partir de
// VITE_API_URL. Cada prueba fija la variable y vuelve a importar el módulo.
async function cargarApi(url: string | undefined) {
  vi.resetModules();
  if (url === undefined) vi.stubEnv("VITE_API_URL", undefined as unknown as string);
  else vi.stubEnv("VITE_API_URL", url);
  return import("./api");
}

const BASE = "https://api.prueba";

function respuesta(estado: number, cuerpo?: unknown): Response {
  const texto = cuerpo === undefined ? "sin JSON" : JSON.stringify(cuerpo);
  return new Response(texto, { status: estado, headers: { "Content-Type": "application/json" } });
}

let fetchFalso: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchFalso = vi.fn();
  vi.stubGlobal("fetch", fetchFalso);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("detallesDeError", () => {
  it("422 de Pydantic: traduce el campo y quita el prefijo 'Value error, '", async () => {
    const { detallesDeError } = await cargarApi(BASE);
    const detail = [
      { loc: ["body", "edad"], msg: "Input should be less than or equal to 120" },
      { loc: ["body", "diagnostico"], msg: "Value error, tipo_mnv solo aplica a DMRE" },
    ];
    expect(detallesDeError(detail)).toEqual([
      "Edad: Input should be less than or equal to 120",
      "Diagnóstico: tipo_mnv solo aplica a DMRE",
    ]);
  });

  it("422 de Pydantic: campo sin traducción, sin campo o sin mensaje", async () => {
    const { detallesDeError } = await cargarApi(BASE);
    expect(detallesDeError([{ loc: ["body", "tabaquismo"], msg: "inválido" }])).toEqual(["tabaquismo: inválido"]);
    expect(detallesDeError([{ loc: ["body"], msg: "Cuerpo inválido" }])).toEqual(["Cuerpo inválido"]);
    expect(detallesDeError([{ loc: ["body", "farmacos_ya_probados", 0], msg: "x" }])).toEqual(["x"]);
    expect(detallesDeError([{ loc: ["body", "edad"] }])).toEqual(["Edad: Valor inválido"]);
  });

  it("errores del Excel: el mensaje primero y después cada error", async () => {
    const { detallesDeError } = await cargarApi(BASE);
    const detail = { mensaje: "El Excel no cumple el esquema RTU.", errores: ["Falta la columna ojo", "edad fuera de rango en la fila 12"] };
    expect(detallesDeError(detail)).toEqual([
      "El Excel no cumple el esquema RTU.",
      "Falta la columna ojo",
      "edad fuera de rango en la fila 12",
    ]);
    expect(detallesDeError({ mensaje: "Solo el mensaje" })).toEqual(["Solo el mensaje"]);
  });

  it("texto simple o forma desconocida", async () => {
    const { detallesDeError } = await cargarApi(BASE);
    expect(detallesDeError("Fármaco desconocido")).toEqual(["Fármaco desconocido"]);
    for (const raro of [undefined, null, 42, { otra: "cosa" }]) {
      expect(detallesDeError(raro)).toEqual(["La API respondió con un error inesperado."]);
    }
  });
});

describe("dirección de la API", () => {
  it("usa VITE_API_URL sin la barra final y no avisa", async () => {
    const { api } = await cargarApi(`${BASE}/`);
    fetchFalso.mockResolvedValue(respuesta(200, { status: "ok" }));
    await api.salud();
    expect(fetchFalso.mock.calls[0][0]).toBe(`${BASE}/health`);
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("sin VITE_API_URL usa la dirección de Render y avisa en la consola", async () => {
    const { api } = await cargarApi(undefined);
    fetchFalso.mockResolvedValue(respuesta(200, { status: "ok" }));
    await api.salud();
    expect(fetchFalso.mock.calls[0][0]).toBe("https://vacunas-mwyr.onrender.com/health");
    expect(console.warn).toHaveBeenCalledOnce();
  });

  it("con VITE_API_URL vacía también usa la dirección de Render", async () => {
    const { api } = await cargarApi("");
    fetchFalso.mockResolvedValue(respuesta(200, { status: "ok" }));
    await api.salud();
    expect(fetchFalso.mock.calls[0][0]).toBe("https://vacunas-mwyr.onrender.com/health");
    expect(console.warn).toHaveBeenCalledOnce();
  });
});

describe("pedidos a la API", () => {
  it("respuesta correcta: envía JSON por POST y devuelve el cuerpo", async () => {
    const { api } = await cargarApi(BASE);
    fetchFalso.mockResolvedValue(respuesta(200, { orden_sugerido: ["FarmacoB"] }));
    const plan = await api.plan(SOLICITUD);
    expect(plan).toEqual({ orden_sugerido: ["FarmacoB"] });
    const [url, opciones] = fetchFalso.mock.calls[0];
    expect(url).toBe(`${BASE}/rtu/sugerir-plan`);
    expect(opciones.method).toBe("POST");
    expect(opciones.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opciones.body)).toEqual(SOLICITUD);
  });

  it("422: ErrorApi con el estado, la ruta y los mensajes traducidos", async () => {
    const { api, ErrorApi } = await cargarApi(BASE);
    fetchFalso.mockResolvedValue(respuesta(422, { detail: [{ loc: ["body", "edad"], msg: "fuera de rango" }] }));
    const error = await api.plan(SOLICITUD).catch((e) => e);
    expect(error).toBeInstanceOf(ErrorApi);
    expect(error.estado).toBe(422);
    expect(error.ruta).toBe("/rtu/sugerir-plan");
    expect(error.detalles).toEqual(["Edad: fuera de rango"]);
    expect(error.message).toBe("Edad: fuera de rango");
  });

  it("400 del Excel y 503 sin histórico: pasan el detalle tal cual", async () => {
    const { api } = await cargarApi(BASE);
    fetchFalso.mockResolvedValueOnce(respuesta(400, { detail: { mensaje: "Excel inválido", errores: ["Falta ojo"] } }));
    await expect(api.plan(SOLICITUD)).rejects.toMatchObject({ estado: 400, detalles: ["Excel inválido", "Falta ojo"] });
    fetchFalso.mockResolvedValueOnce(respuesta(503, { detail: "No hay histórico RTU cargado." }));
    await expect(api.info()).rejects.toMatchObject({ estado: 503, detalles: ["No hay histórico RTU cargado."] });
  });

  it("error sin cuerpo JSON: mensaje genérico, sin romperse", async () => {
    const { api } = await cargarApi(BASE);
    fetchFalso.mockResolvedValue(respuesta(500));
    await expect(api.info()).rejects.toMatchObject({
      estado: 500,
      detalles: ["La API respondió con un error inesperado."],
    });
  });

  it("404: explica que el servidor tiene una versión anterior, aunque traiga detalle", async () => {
    const { api } = await cargarApi(BASE);
    fetchFalso.mockResolvedValue(respuesta(404, { detail: "Not Found" }));
    const error = await api.info().catch((e) => e);
    expect(error.estado).toBe(404);
    expect(error.ruta).toBe("/rtu/info");
    expect(error.detalles).toHaveLength(1);
    expect(error.detalles[0]).toContain(`La API en ${BASE} respondió, pero no tiene /rtu/info`);
    expect(error.detalles[0]).toContain("Render");
  });

  it("sin conexión: estado 0 y la dirección a la que intentó conectarse", async () => {
    const { api } = await cargarApi(BASE);
    fetchFalso.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await api.salud().catch((e) => e);
    expect(error.estado).toBe(0);
    expect(error.detalles).toEqual([
      `No se pudo conectar con la API en ${BASE}. Verificá que esté en funcionamiento y que haya conexión.`,
    ]);
  });

  it("tiempo de espera agotado: corta el pedido y lo explica", async () => {
    vi.useFakeTimers();
    const { api } = await cargarApi(BASE);
    // fetch que nunca responde, salvo que lo aborten
    fetchFalso.mockImplementation((_url: string, opciones: RequestInit) =>
      new Promise((_res, rechazar) => {
        opciones.signal?.addEventListener("abort", () =>
          rechazar(new DOMException("The operation was aborted.", "AbortError")));
      }));
    const pedido = api.info().catch((e) => e);
    await vi.advanceTimersByTimeAsync(89_999);
    expect(fetchFalso.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await pedido;
    expect(error.estado).toBe(0);
    expect(error.detalles[0]).toMatch(/^El servidor tardó demasiado en responder/);
  });

  it("cada endpoint tiene su propio tiempo de espera", async () => {
    vi.useFakeTimers();
    const { api } = await cargarApi(BASE);
    const señales: AbortSignal[] = [];
    fetchFalso.mockImplementation((_url: string, opciones: RequestInit) => {
      señales.push(opciones.signal!);
      return new Promise(() => {});
    });
    void api.salud();    // 120 s
    void api.compra(COMPRA); // 300 s
    await vi.advanceTimersByTimeAsync(119_999);
    expect(señales.map((s) => s.aborted)).toEqual([false, false]);
    await vi.advanceTimersByTimeAsync(1);
    expect(señales.map((s) => s.aborted)).toEqual([true, false]);
    await vi.advanceTimersByTimeAsync(180_000);
    expect(señales.map((s) => s.aborted)).toEqual([true, true]);
  });
});
