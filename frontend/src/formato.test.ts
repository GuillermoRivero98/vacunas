import { describe, expect, it } from "vitest";
import { comorbilidades, datosPaciente, desenlace, fechaHora, nombreArchivoPlan, nombreFarmaco, num, pct, subtipo } from "./formato";
import type { SolicitudPlan } from "./tipos";

// Los números se formatean con la configuración regional es-UY: coma decimal
// y punto de miles. Requiere un Node con ICU completo (las versiones oficiales lo traen).

describe("pct", () => {
  it("convierte una proporción en porcentaje sin decimales por defecto", () => {
    expect(pct(0.5)).toBe("50%");
    expect(pct(0)).toBe("0%");
    expect(pct(1)).toBe("100%");
  });

  it("redondea a los decimales pedidos, con coma decimal", () => {
    expect(pct(0.1234, 1)).toBe("12,3%");
    expect(pct(0.473)).toBe("47%");
  });
});

describe("num", () => {
  it("usa punto de miles y coma decimal", () => {
    expect(num(1856)).toBe("1.856");
    expect(num(34268)).toBe("34.268");
    expect(num(1234.56, 1)).toBe("1.234,6");
  });

  it("completa los decimales pedidos", () => {
    expect(num(2, 1)).toBe("2,0");
    expect(num(25.26, 1)).toBe("25,3");
  });
});

describe("nombreFarmaco", () => {
  it("separa el prefijo Farmaco y le agrega la tilde", () => {
    expect(nombreFarmaco("FarmacoB")).toBe("Fármaco B");
  });

  it("deja igual un nombre que no empieza con Farmaco", () => {
    expect(nombreFarmaco("Aflibercept")).toBe("Aflibercept");
  });
});

describe("desenlace", () => {
  it("traduce los desenlaces conocidos", () => {
    expect(desenlace("estable")).toBe("estable");
    expect(desenlace("switch")).toBe("cambió de fármaco");
    expect(desenlace("abandono")).toBe("abandonó");
  });

  it("muestra censurado y en_curso como tratamiento en curso", () => {
    expect(desenlace("censurado")).toBe("sigue en tratamiento");
    expect(desenlace("en_curso")).toBe("sigue en tratamiento");
  });

  it("deja pasar un desenlace desconocido sin romperse", () => {
    expect(desenlace("otro")).toBe("otro");
  });
});

describe("subtipo", () => {
  it("traduce los subtipos del sistema", () => {
    expect(subtipo("DMRE-MNV2")).toBe("DMRE, MNV tipo 2");
    expect(subtipo("EMD")).toBe("Edema macular diabético");
    expect(subtipo("ORVR")).toBe("Oclusión de rama venosa");
  });

  it("deja pasar un subtipo desconocido", () => {
    expect(subtipo("XYZ")).toBe("XYZ");
  });
});

describe("comorbilidades", () => {
  it("lista solo las presentes, en el orden recibido", () => {
    expect(comorbilidades({ diabetes: 1, hipertension: 0, tabaquismo: 1 })).toBe("diabetes, tabaquismo");
    expect(comorbilidades({ acv_iam_reciente: 1 })).toBe("ACV o infarto reciente");
  });

  it("avisa cuando no hay ninguna registrada", () => {
    expect(comorbilidades({ diabetes: 0, hipertension: 0 })).toBe("sin comorbilidades registradas");
    expect(comorbilidades({})).toBe("sin comorbilidades registradas");
  });

  it("deja pasar una comorbilidad desconocida", () => {
    expect(comorbilidades({ glaucoma: 1 })).toBe("glaucoma");
  });
});

// ---------- Reporte impreso (RF-16) ----------
// Las fechas se construyen con la hora local, así el resultado no depende del huso horario de la máquina.

describe("fechaHora y nombreArchivoPlan", () => {
  const d = new Date(2026, 8, 3, 7, 5); // 3 de septiembre de 2026, 07:05

  it("formatea la fecha como dd/mm/aaaa hh:mm, con ceros a la izquierda", () => {
    expect(fechaHora(d)).toBe("03/09/2026 07:05");
  });

  it("arma un nombre de archivo ordenable y sin caracteres problemáticos", () => {
    expect(nombreArchivoPlan(d)).toBe("Plan intravitreo 2026-09-03 0705");
    expect(nombreArchivoPlan(d)).not.toMatch(/[\\/:*?"<>|áéíóú]/);
  });
});

describe("datosPaciente", () => {
  const base: SolicitudPlan = {
    diagnostico: "DMRE", edad: 72, farmacos_ya_probados: [], objetivo: "estable", n_casos_similares: 5,
  };
  const valor = (s: SolicitudPlan, etiqueta: string) => datosPaciente(s).find((d) => d.etiqueta === etiqueta)?.valor;

  it("DMRE con tipo de membrana, primera línea y sin antecedentes enviados", () => {
    expect(datosPaciente({ ...base, tipo_mnv: "MNV2" })).toEqual([
      { etiqueta: "Diagnóstico", valor: "DMRE exudativa" },
      { etiqueta: "Tipo de membrana", valor: "Tipo 2" },
      { etiqueta: "Edad", valor: "72 años" },
      { etiqueta: "Antecedentes", valor: "Sin datos completos: el cálculo no usó la carga de comorbilidades" },
      { etiqueta: "Fármacos ya recibidos", valor: "Ninguno" },
      { etiqueta: "Prioridad", valor: "Más chances de estabilizarse" },
    ]);
  });

  it("DMRE sin tipo de membrana: lo informa como sin dato", () => {
    expect(valor(base, "Tipo de membrana")).toBe("Sin dato");
  });

  it("fuera de DMRE no muestra el tipo de membrana", () => {
    const filas = datosPaciente({ ...base, diagnostico: "OVCR" });
    expect(filas.map((f) => f.etiqueta)).not.toContain("Tipo de membrana");
    expect(filas[0].valor).toBe("Oclusión de vena central");
  });

  it("antecedentes completos: lista los presentes", () => {
    const s: SolicitudPlan = { ...base, diagnostico: "EMD", diabetes: 1, hipertension: 1, acv_iam_reciente: 0, tabaquismo: 0 };
    expect(valor(s, "Antecedentes")).toBe("diabetes, hipertensión");
  });

  it("antecedentes completos y todos negativos: Ninguno", () => {
    const s: SolicitudPlan = { ...base, diabetes: 0, hipertension: 0, acv_iam_reciente: 0, tabaquismo: 0 };
    expect(valor(s, "Antecedentes")).toBe("Ninguno");
  });

  it("antecedentes incompletos: aclara que el cálculo no los usó", () => {
    const s: SolicitudPlan = { ...base, diabetes: 1, hipertension: 0 };
    expect(valor(s, "Antecedentes")).toBe("Sin datos completos: el cálculo no usó la carga de comorbilidades");
  });

  it("fármacos ya recibidos y prioridad por inyecciones", () => {
    const s: SolicitudPlan = { ...base, farmacos_ya_probados: ["FarmacoA", "FarmacoC"], objetivo: "inyecciones" };
    expect(valor(s, "Fármacos ya recibidos")).toBe("Fármaco A, Fármaco C");
    expect(valor(s, "Prioridad")).toBe("Menos inyecciones");
  });
});
