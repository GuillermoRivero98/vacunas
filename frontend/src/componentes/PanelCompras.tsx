import { useState } from "react";
import type { Compra, SolicitudCompra } from "../tipos";
import { nombreFarmaco, num, pct } from "../formato";
import { Aviso } from "./Aviso";

const HORIZONTES = [
  { semanas: 13, texto: "3 meses" },
  { semanas: 26, texto: "6 meses" },
  { semanas: 52, texto: "1 año" },
  { semanas: 104, texto: "2 años" },
];
const NIVELES = [0.8, 0.9, 0.95, 0.99];
export const COMPRA_POR_DEFECTO: SolicitudCompra = { horizonte_semanas: 52, nivel_servicio: 0.95, nuevos_ojos_por_semana: 0 };

interface Props {
  compra: Compra | null;
  ocupado: boolean;
  habilitado: boolean;
  onCalcular: (s: SolicitudCompra) => void;
}

export function PanelCompras({ compra, ocupado, habilitado, onCalcular }: Props) {
  const [s, setS] = useState<SolicitudCompra>(COMPRA_POR_DEFECTO);
  const nuevosValido = Number.isFinite(s.nuevos_ojos_por_semana) && s.nuevos_ojos_por_semana >= 0 && s.nuevos_ojos_por_semana <= 1000;
  const esDefecto = s.horizonte_semanas === 52 && s.nivel_servicio === 0.95 && s.nuevos_ojos_por_semana === 0;
  const horizonte = HORIZONTES.find((h) => h.semanas === compra?.horizonte_semanas)?.texto ?? `${compra?.horizonte_semanas} semanas`;
  const farmacos = compra ? Object.keys(compra.por_farmaco) : [];

  return (
    <div className="compras">
      <form className="compras__controles" onSubmit={(e) => { e.preventDefault(); if (nuevosValido) onCalcular(s); }}>
        <div className="campo">
          <label htmlFor="horizonte">Período a cubrir</label>
          <select id="horizonte" value={s.horizonte_semanas}
            onChange={(e) => setS({ ...s, horizonte_semanas: Number(e.target.value) })}>
            {HORIZONTES.map((h) => <option key={h.semanas} value={h.semanas}>{h.texto}</option>)}
          </select>
        </div>
        <div className="campo">
          <label htmlFor="nivel">Probabilidad de que alcance</label>
          <select id="nivel" value={s.nivel_servicio}
            onChange={(e) => setS({ ...s, nivel_servicio: Number(e.target.value) })}>
            {NIVELES.map((n) => <option key={n} value={n}>{pct(n)}</option>)}
          </select>
        </div>
        <div className="campo">
          <label htmlFor="nuevos">Ojos nuevos por semana</label>
          <input id="nuevos" type="number" min={0} max={1000} step={0.5} value={s.nuevos_ojos_por_semana}
            onChange={(e) => setS({ ...s, nuevos_ojos_por_semana: Number(e.target.value) })} aria-invalid={!nuevosValido} />
        </div>
        <button type="submit" className="boton" disabled={!habilitado || ocupado || !nuevosValido}>
          {ocupado ? "Calculando…" : "Calcular compra"}
        </button>
        {!esDefecto && !ocupado && (
          <p className="ayuda compras__nota">
            1 año, 95% y sin ojos nuevos está precalculado. Otras combinaciones se calculan en el momento
            y, si cambiás el período, pueden tardar un par de minutos.
          </p>
        )}
      </form>

      {!compra && !ocupado && <p className="vacio">Elegí el período y calculá cuántas dosis de cada fármaco conviene comprar.</p>}

      {compra && (
        <section className="resultado" aria-live="polite">
          <header className="resultado__encabezado">
            <h2>Compra sugerida para {horizonte}</h2>
            <p className="resultado__bajada">
              Con {pct(compra.nivel_servicio)} de probabilidad, estas cantidades alcanzan para los{" "}
              {num(compra.ojos_en_tratamiento)} ojos que hoy están en tratamiento
              {compra.nuevos_ojos_por_semana > 0 && <> más {num(compra.nuevos_ojos_por_semana, 1)} ojos nuevos por semana</>}.
            </p>
          </header>

          <Aviso items={compra.advertencias} />

          <div className="tabla-marco">
          <table className="tabla tabla--compras">
            <thead>
              <tr>
                <th scope="col">Fármaco</th>
                <th scope="col" className="num">Comprar</th>
                <th scope="col" className="num">Uso esperado</th>
                <th scope="col" className="num">Rango probable (95%)</th>
                <th scope="col" className="num">De pacientes actuales</th>
                <th scope="col" className="num">De pacientes nuevos</th>
                <th scope="col" className="num">Uso histórico</th>
              </tr>
            </thead>
            <tbody>
              {farmacos.map((f) => {
                const c = compra.por_farmaco[f];
                const u = compra.uso_historico[f];
                return (
                  <tr key={f}>
                    <th scope="row">{nombreFarmaco(f)}</th>
                    <td className="num compra__cifra">{num(c.compra_sugerida)}</td>
                    <td className="num">{num(c.demanda_esperada)}</td>
                    <td className="num">{num(c.intervalo_95[0])} a {num(c.intervalo_95[1])}</td>
                    <td className="num">{num(c.de_ojos_en_tratamiento)}</td>
                    <td className="num">{num(c.de_pacientes_nuevos)}</td>
                    <td className="num">
                      <span className="proporcion">
                        <span className="proporcion__barra" aria-hidden="true">
                          <span style={{ width: `${(u.proporcion_del_total * 100).toFixed(1)}%` }} />
                        </span>
                        {pct(u.proporcion_del_total)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <p className="ayuda">
            Dosis = inyecciones. Cada ojo nuevo que ingresa suma, en promedio en este período:{" "}
            {farmacos.map((f, i) => (
              <span key={f}>{i > 0 && (i === farmacos.length - 1 ? " y " : ", ")}
                {num(compra.por_farmaco[f].por_cada_ojo_nuevo_en_el_horizonte, 1)} de {nombreFarmaco(f)}</span>
            ))}.
          </p>

          <details className="desplegable">
            <summary>Cómo se calculó</summary>
            <p>
              Para cada ojo en tratamiento se calcula, con su estado actual en el protocolo y su probabilidad
              de actividad, la esperanza y la varianza de cuántas inyecciones de cada fármaco va a recibir.
              Se suman todos los ojos y la compra es el uso esperado más un margen según la probabilidad elegida.
            </p>
            <p>
              El cálculo se corrige con el propio histórico: se repitió el pronóstico en {compra.calibracion.ventanas.length}{" "}
              períodos pasados y se comparó con lo que realmente se usó. Corrección del uso esperado:{" "}
              <strong>×{num(compra.calibracion.factor, 3)}</strong>; ampliación del margen:{" "}
              <strong>×{num(compra.calibracion.inflacion, 2)}</strong>.
            </p>
            {compra.calibracion.backtests.length > 0 && (
              <>
              <p className="tabla-titulo" id="titulo-backtests">Pronósticos de prueba en períodos pasados</p>
              <div className="tabla-marco">
              <table className="tabla tabla--chica" aria-labelledby="titulo-backtests">
                <thead>
                  <tr><th scope="col">Desde la semana</th><th scope="col">Fármaco</th><th scope="col" className="num">Pronosticado</th><th scope="col" className="num">Usado</th><th scope="col" className="num">Diferencia</th></tr>
                </thead>
                <tbody>
                  {compra.calibracion.backtests.map((b) => (
                    <tr key={`${b.corte}-${b.farmaco}`}>
                      <td>{b.corte}</td><td>{nombreFarmaco(b.farmaco)}</td>
                      <td className="num">{num(b.predicho)}</td><td className="num">{num(b.real)}</td>
                      <td className="num">{b["error_%"] === null ? "sin datos" : `${b["error_%"] > 0 ? "+" : ""}${num(b["error_%"], 1)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              </>
            )}
            <p className="ayuda">Supuestos: {compra.supuestos.join("; ")}.</p>
          </details>
        </section>
      )}
    </div>
  );
}
