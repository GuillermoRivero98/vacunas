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
