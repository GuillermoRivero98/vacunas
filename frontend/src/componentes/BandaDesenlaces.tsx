import { pct } from "../formato";

interface Props {
  estable: number;
  cambio: number;
  abandono: number;
  compacta?: boolean;
}

/** Los tres desenlaces posibles de un fármaco suman 100%: una banda
 *  dividida en tramos, como las capas de un corte de OCT. */
export function BandaDesenlaces({ estable, cambio, abandono, compacta = false }: Props) {
  const tramos = [
    { clase: "estable", valor: estable, texto: "se estabiliza" },
    { clase: "cambio", valor: cambio, texto: "cambia de fármaco" },
    { clase: "abandono", valor: abandono, texto: "abandona" },
  ];
  const descripcion = tramos.map((t) => `${pct(t.valor)} ${t.texto}`).join(", ");
  return (
    <figure className={`banda${compacta ? " banda--compacta" : ""}`}>
      <div className="banda__barra" role="img" aria-label={descripcion}>
        {tramos.map((t) => (
          <span key={t.clase} className={`banda__tramo banda__tramo--${t.clase}`} style={{ flexGrow: t.valor }} />
        ))}
      </div>
      {!compacta && (
        <figcaption className="banda__leyenda">
          {tramos.map((t) => (
            <span key={t.clase} className={`banda__dato banda__dato--${t.clase}`}>
              <strong>{pct(t.valor)}</strong> {t.texto}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}
