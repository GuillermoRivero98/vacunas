import type { Plan } from "../tipos";
import { comorbilidades, desenlace, nombreFarmaco, num, pct, subtipo } from "../formato";
import { BandaDesenlaces } from "./BandaDesenlaces";
import { Aviso } from "./Aviso";

const ADVERTENCIA_MEDICO = "La decisión final es del médico tratante.";

export function ResultadoPlan({ plan }: { plan: Plan }) {
  const [primero] = plan.orden_sugerido;
  const m1 = plan.por_farmaco.find((f) => f.farmaco === primero)!;
  const avisos = plan.advertencias.filter((a) => a !== ADVERTENCIA_MEDICO);

  return (
    <section className="resultado" aria-live="polite">
      <header className="resultado__encabezado">
        <h2>Probar primero {nombreFarmaco(primero)}</h2>
        <p className="resultado__bajada">
          Probabilidad estimada de que el ojo se estabilice con este fármaco: <strong>{pct(m1.prob_estable)}</strong>.
          {plan.linea > 1 && <> Se calculó como fármaco de rescate (línea {plan.linea}).</>}
        </p>
      </header>

      <Aviso items={avisos} />

      <ol className="ranking">
        {plan.orden_sugerido.map((f) => {
          const m = plan.por_farmaco.find((x) => x.farmaco === f)!;
          return (
            <li key={f} className="ranking__item">
              <div className="ranking__cabeza">
                <h3>{nombreFarmaco(f)}</h3>
                <p className="ranking__cifras">
                  <span><strong>{num(m.inyecciones_esperadas, 1)}</strong> inyecciones esperadas</span>
                  <span><strong>{num(m.semanas_esperadas / 52, 1)}</strong> años de tratamiento esperados</span>
                </p>
              </div>
              <BandaDesenlaces estable={m.prob_estable} cambio={m.prob_switch} abandono={m.prob_abandono} />
            </li>
          );
        })}
      </ol>

      <p className="resultado__secuencia">
        Siguiendo este orden, la probabilidad de estabilizarse con alguno de los fármacos es{" "}
        <strong>{pct(plan.valor_orden.prob_estable)}</strong>, con{" "}
        <strong>{num(plan.valor_orden.inyecciones_esperadas, 1)}</strong> inyecciones esperadas en total.
      </p>

      <section className="base">
        <h2>En qué se basa</h2>
        <p>
          Estas probabilidades salen de los {num(plan.version_modelo.ojos)} ojos del histórico. Para mostrar
          casos concretos, se buscaron los más parecidos a este paciente (mismo diagnóstico, edad y
          antecedentes cercanos) y se revisó cómo les fue con cada fármaco.
        </p>
        <p className="tabla-titulo" id="titulo-base">
          Tratamientos con cada fármaco entre los {plan.base_de_calculo[primero].ojos_similares_considerados} ojos más parecidos
        </p>
        <div className="tabla-marco">
        <table className="tabla" aria-labelledby="titulo-base">
          <thead>
            <tr>
              <th scope="col">Fármaco</th>
              <th scope="col" className="num">Tratados</th>
              <th scope="col" className="num">Estables</th>
              <th scope="col" className="num">Cambiaron</th>
              <th scope="col" className="num">Abandonaron</th>
              <th scope="col" className="num">Siguen</th>
            </tr>
          </thead>
          <tbody>
            {plan.orden_sugerido.map((f) => {
              const b = plan.base_de_calculo[f];
              const d = b.desenlaces;
              return (
                <tr key={f}>
                  <th scope="row">{nombreFarmaco(f)}</th>
                  <td className="num">{b.ciclos_con_este_farmaco}</td>
                  <td className="num">{d.estable ?? 0}</td>
                  <td className="num">{d.switch ?? 0}</td>
                  <td className="num">{d.abandono ?? 0}</td>
                  <td className="num">{(d.censurado ?? 0) + (d.en_curso ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        <p className="ayuda">
          Controles con la enfermedad activa (intervalo de 6 a 8 semanas), en los casos parecidos frente a lo
          estimado para este paciente:{" "}
          {plan.orden_sugerido.map((f, i) => {
            const b = plan.base_de_calculo[f];
            const texto = b.actividad_observada_en_similares_q6_8 !== undefined
              ? `${pct(b.actividad_observada_en_similares_q6_8)} frente a ${pct(b.actividad_estimada_por_modelo_q6_8 ?? 0)}`
              : "pocos datos";
            return <span key={f}>{i > 0 && "; "}{nombreFarmaco(f)}, {texto}</span>;
          })}
          . Si difieren mucho, se avisa arriba.
        </p>

        <h3 className="base__titulo">Casos más parecidos</h3>
        <ul className="casos">
          {plan.casos_similares.map((c) => (
            <li key={c.id} className="caso">
              <div className="caso__perfil">
                <span className="caso__id">{c.id}</span>
                <span>{subtipo(c.subtipo)}, {c.edad} años</span>
                <span className="caso__comorb">{comorbilidades(c.comorbilidades)}</span>
              </div>
              <ol className="caso__trayectoria" aria-label="Fármacos recibidos, en orden">
                {c.trayectoria.map((t) => (
                  <li key={t.linea} className={`tramo tramo--${t.desenlace}`}>
                    <strong>{nombreFarmaco(t.farmaco)}</strong>
                    <span>{t.inyecciones} inyecciones en {num(t.semanas / 52, 1)} años, {desenlace(t.desenlace)}</span>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      </section>

      <details className="desplegable">
        <summary>Supuestos del protocolo usados en el cálculo</summary>
        <dl className="supuestos">
          <dt>Carga</dt><dd>{plan.supuestos.dosis_carga} dosis cada {plan.supuestos.intervalo_carga_semanas} semanas</dd>
          <dt>Si está seco</dt><dd>se extiende {plan.supuestos.paso_extension_semanas} semanas</dd>
          <dt>Si está activo</dt><dd>se acorta {plan.supuestos.paso_acortamiento_semanas} semanas</dd>
          <dt>Intervalos</dt><dd>entre {plan.supuestos.intervalo_min_semanas} y {plan.supuestos.intervalo_max_semanas} semanas</dd>
          <dt>Cambio de fármaco</dt><dd>{plan.supuestos.visitas_activas_en_min_para_switch} controles activos seguidos en el intervalo mínimo</dd>
          <dt>Estabilidad</dt><dd>{plan.supuestos.visitas_secas_en_max_para_estabilidad} controles secos seguidos en el intervalo máximo</dd>
          <dt>Abandono</dt><dd>{pct(plan.supuestos.prob_abandono_por_visita)} por visita</dd>
        </dl>
      </details>
    </section>
  );
}
