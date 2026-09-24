interface Props {
  tipo?: "error" | "nota";
  titulo?: string;
  items: string[];
}

export function Aviso({ tipo = "nota", titulo, items }: Props) {
  if (!items.length) return null;
  return (
    <div className={`aviso aviso--${tipo}`} role={tipo === "error" ? "alert" : "note"}>
      {titulo && <p className="aviso__titulo">{titulo}</p>}
      {items.length === 1 ? <p>{items[0]}</p> : <ul>{items.map((t) => <li key={t}>{t}</li>)}</ul>}
    </div>
  );
}
