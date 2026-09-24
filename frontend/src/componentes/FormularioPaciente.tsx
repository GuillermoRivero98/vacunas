import { useState } from "react";
import type { Diagnostico, Objetivo, SolicitudPlan, TipoMNV } from "../tipos";
import { nombreFarmaco } from "../formato";

type TriEstado = "si" | "no" | "sd";

const DIAGNOSTICOS: { valor: Diagnostico; texto: string }[] = [
  { valor: "DMRE", texto: "DMRE exudativa" },
  { valor: "EMD", texto: "Edema macular diabético" },
  { valor: "OVCR", texto: "Oclusión de vena central" },
  { valor: "ORVR", texto: "Oclusión de rama venosa" },
];

const COMORBILIDADES = [
  { clave: "diabetes", texto: "Diabetes" },
  { clave: "hipertension", texto: "Hipertensión" },
  { clave: "acv_iam_reciente", texto: "ACV o infarto en los últimos 3 meses" },
  { clave: "tabaquismo", texto: "Tabaquismo" },
] as const;
type Comorbilidad = (typeof COMORBILIDADES)[number]["clave"];

interface Props {
  farmacos: string[];
  ocupado: boolean;
  habilitado: boolean;
  onCalcular: (s: SolicitudPlan) => void;
}

export function FormularioPaciente({ farmacos, ocupado, habilitado, onCalcular }: Props) {
  const [diagnostico, setDiagnostico] = useState<Diagnostico>("DMRE");
  const [tipoMnv, setTipoMnv] = useState<TipoMNV | "">("");
  const [edad, setEdad] = useState("70");
  const [comorb, setComorb] = useState<Record<Comorbilidad, TriEstado>>({
    diabetes: "sd", hipertension: "sd", acv_iam_reciente: "sd", tabaquismo: "sd",
  });
  const [probados, setProbados] = useState<string[]>([]);
  const [objetivo, setObjetivo] = useState<Objetivo>("estable");

  const edadNum = Number(edad);
  const edadValida = edad.trim() !== "" && Number.isInteger(edadNum) && edadNum >= 18 && edadNum <= 110;
  const quedanCandidatos = farmacos.length === 0 || probados.length < farmacos.length;
  const puedeEnviar = habilitado && !ocupado && edadValida && quedanCandidatos;

  const elegirDiagnostico = (d: Diagnostico) => {
    setDiagnostico(d);
    if (d !== "DMRE") setTipoMnv("");
    if (d === "EMD") setComorb((c) => ({ ...c, diabetes: "si" })); // el EMD implica diabetes
  };

  const enviar = (e: React.FormEvent) => {
    e.preventDefault();
    if (!puedeEnviar) return;
    const s: SolicitudPlan = {
      diagnostico, edad: edadNum, farmacos_ya_probados: probados, objetivo, n_casos_similares: 5,
    };
    if (diagnostico === "DMRE" && tipoMnv) s.tipo_mnv = tipoMnv;
    // Las comorbilidades solo se envían si se conocen TODAS: el modelo usa
    // el conteo total; si falta alguna, marginaliza ese dato.
    const todas = COMORBILIDADES.every(({ clave }) => comorb[clave] !== "sd");
    if (todas) for (const { clave } of COMORBILIDADES) s[clave] = comorb[clave] === "si" ? 1 : 0;
    onCalcular(s);
  };

  return (
    <form className="formulario" onSubmit={enviar} noValidate>
      <fieldset>
        <legend>Diagnóstico</legend>
        <div className="opciones opciones--columna">
          {DIAGNOSTICOS.map((d) => (
            <label key={d.valor} className="opcion">
              <input type="radio" name="diagnostico" checked={diagnostico === d.valor}
                onChange={() => elegirDiagnostico(d.valor)} />
              {d.texto}
            </label>
          ))}
        </div>
      </fieldset>

      {diagnostico === "DMRE" && (
        <fieldset>
          <legend>Tipo de membrana neovascular</legend>
          <div className="segmentado">
            {(["MNV1", "MNV2", "MNV3"] as TipoMNV[]).map((t) => (
              <label key={t}>
                <input type="radio" name="mnv" checked={tipoMnv === t} onChange={() => setTipoMnv(t)} />
                <span>{t.replace("MNV", "Tipo ")}</span>
              </label>
            ))}
            <label>
              <input type="radio" name="mnv" checked={tipoMnv === ""} onChange={() => setTipoMnv("")} />
              <span>No sé</span>
            </label>
          </div>
        </fieldset>
      )}

      <div className="campo">
        <label htmlFor="edad">Edad</label>
        <input id="edad" type="number" inputMode="numeric" min={18} max={110} value={edad}
          onChange={(e) => setEdad(e.target.value)} aria-invalid={!edadValida}
          aria-describedby={!edadValida ? "edad-error" : undefined} />
        {!edadValida && <p id="edad-error" className="campo__error">Ingresá una edad entera entre 18 y 110 años.</p>}
      </div>

      <fieldset>
        <legend>Antecedentes</legend>
        <p className="ayuda">Si falta alguno, el cálculo no usa la carga de comorbilidades.</p>
        {COMORBILIDADES.map(({ clave, texto }) => {
          const bloqueado = clave === "diabetes" && diagnostico === "EMD";
          return (
            <div key={clave} className="comorbilidad">
              <span id={`lbl-${clave}`}>{texto}</span>
              <div className="segmentado segmentado--chico" role="radiogroup" aria-labelledby={`lbl-${clave}`}>
                {(["si", "no", "sd"] as TriEstado[]).map((v) => (
                  <label key={v}>
                    <input type="radio" name={clave} checked={comorb[clave] === v} disabled={bloqueado && v !== "si"}
                      onChange={() => setComorb((c) => ({ ...c, [clave]: v }))} />
                    <span>{v === "si" ? "Sí" : v === "no" ? "No" : "Sin dato"}</span>
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </fieldset>

      {farmacos.length > 0 && (
        <fieldset>
          <legend>Fármacos que ya recibió este ojo</legend>
          <div className="opciones">
            {farmacos.map((f) => (
              <label key={f} className="opcion">
                <input type="checkbox" checked={probados.includes(f)}
                  onChange={(e) => setProbados((p) => (e.target.checked ? [...p, f] : p.filter((x) => x !== f)))} />
                {nombreFarmaco(f)}
              </label>
            ))}
          </div>
          {!quedanCandidatos && <p className="campo__error">Ya probó todos los fármacos disponibles: no hay nada para recomendar.</p>}
        </fieldset>
      )}

      <fieldset>
        <legend>Priorizar</legend>
        <div className="opciones opciones--columna">
          <label className="opcion">
            <input type="radio" name="objetivo" checked={objetivo === "estable"} onChange={() => setObjetivo("estable")} />
            Más chances de estabilizarse
          </label>
          <label className="opcion">
            <input type="radio" name="objetivo" checked={objetivo === "inyecciones"} onChange={() => setObjetivo("inyecciones")} />
            Menos inyecciones
          </label>
        </div>
      </fieldset>

      <button type="submit" className="boton" disabled={!puedeEnviar}>
        {ocupado ? "Calculando…" : "Calcular plan"}
      </button>
    </form>
  );
}
