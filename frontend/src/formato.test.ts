import { describe, expect, it } from "vitest";
import { comorbilidades, desenlace, nombreFarmaco, num, pct, subtipo } from "./formato";

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
