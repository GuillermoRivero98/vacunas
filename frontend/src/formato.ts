import type { SolicitudPlan } from "./tipos";

export const pct = (x: number, dec = 0) =>
  `${(x * 100).toLocaleString("es-UY", { minimumFractionDigits: dec, maximumFractionDigits: dec })}%`;

export const num = (x: number, dec = 0) =>
  x.toLocaleString("es-UY", { minimumFractionDigits: dec, maximumFractionDigits: dec });

/** "FarmacoB" -> "Fármaco B" */
export const nombreFarmaco = (f: string) => f.replace(/^Farmaco/, "Fármaco ");

const DESENLACES: Record<string, string> = {
  estable: "estable",
  switch: "cambió de fármaco",
  abandono: "abandonó",
  censurado: "sigue en tratamiento",
  en_curso: "sigue en tratamiento",
};
export const desenlace = (d: string) => DESENLACES[d] ?? d;

const SUBTIPOS: Record<string, string> = {
  "DMRE-MNV1": "DMRE, MNV tipo 1",
  "DMRE-MNV2": "DMRE, MNV tipo 2",
  "DMRE-MNV3": "DMRE, MNV tipo 3",
  DMRE: "DMRE",
  EMD: "Edema macular diabético",
  OVCR: "Oclusión de vena central",
  ORVR: "Oclusión de rama venosa",
};
export const subtipo = (s: string) => SUBTIPOS[s] ?? s;

const COMORB: Record<string, string> = {
  diabetes: "diabetes",
  hipertension: "hipertensión",
  acv_iam_reciente: "ACV o infarto reciente",
  tabaquismo: "tabaquismo",
};
export const comorbilidades = (c: Record<string, number>) => {
  const si = Object.entries(c).filter(([, v]) => v === 1).map(([k]) => COMORB[k] ?? k);
  return si.length ? si.join(", ") : "sin comorbilidades registradas";
};

// ---------- Reporte impreso de una recomendación (RF-16) ----------

const dos = (n: number) => String(n).padStart(2, "0");

/** Fecha y hora locales en formato uruguayo: "28/09/2026 15:30". */
export const fechaHora = (d: Date) =>
  `${dos(d.getDate())}/${dos(d.getMonth() + 1)}/${d.getFullYear()} ${dos(d.getHours())}:${dos(d.getMinutes())}`;

/** Nombre que propone el navegador al guardar como PDF (sin tildes ni barras). */
export const nombreArchivoPlan = (d: Date) =>
  `Plan intravitreo ${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())} ${dos(d.getHours())}${dos(d.getMinutes())}`;

const DIAGNOSTICOS: Record<string, string> = {
  DMRE: "DMRE exudativa",
  EMD: "Edema macular diabético",
  OVCR: "Oclusión de vena central",
  ORVR: "Oclusión de rama venosa",
};

const OBJETIVOS: Record<string, string> = {
  estable: "Más chances de estabilizarse",
  inyecciones: "Menos inyecciones",
};

const CLAVES_COMORB = ["diabetes", "hipertension", "acv_iam_reciente", "tabaquismo"] as const;

/** Los datos del paciente TAL COMO LOS USÓ EL CÁLCULO. El formulario solo manda
 *  los antecedentes si se conocen los cuatro; si no, el modelo los marginaliza
 *  y el reporte lo dice, aunque en pantalla se haya marcado alguno. */
export function datosPaciente(s: SolicitudPlan): { etiqueta: string; valor: string }[] {
  const filas = [{ etiqueta: "Diagnóstico", valor: DIAGNOSTICOS[s.diagnostico] ?? s.diagnostico }];
  if (s.diagnostico === "DMRE") {
    filas.push({ etiqueta: "Tipo de membrana", valor: s.tipo_mnv ? s.tipo_mnv.replace("MNV", "Tipo ") : "Sin dato" });
  }
  filas.push({ etiqueta: "Edad", valor: `${s.edad} años` });

  const conocidas = CLAVES_COMORB.filter((k) => s[k] !== undefined);
  let antecedentes: string;
  if (conocidas.length < CLAVES_COMORB.length) {
    antecedentes = "Sin datos completos: el cálculo no usó la carga de comorbilidades";
  } else {
    const valores = Object.fromEntries(CLAVES_COMORB.map((k) => [k, s[k] as number]));
    antecedentes = CLAVES_COMORB.some((k) => s[k] === 1) ? comorbilidades(valores) : "Ninguno";
  }
  filas.push({ etiqueta: "Antecedentes", valor: antecedentes });

  filas.push({
    etiqueta: "Fármacos ya recibidos",
    valor: s.farmacos_ya_probados.length ? s.farmacos_ya_probados.map(nombreFarmaco).join(", ") : "Ninguno",
  });
  filas.push({ etiqueta: "Prioridad", valor: OBJETIVOS[s.objetivo] ?? s.objetivo });
  return filas;
}
