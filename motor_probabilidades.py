"""
Motor numérico determinístico para la decisión de orden de vacunas.

Todo acá es matemática auditable (pandas + scipy + numpy). No hay
ningún componente de LLM en este archivo: dado el mismo Excel y el
mismo caso, esto siempre devuelve exactamente el mismo resultado.

Pipeline:
  1. Filtrar "casos similares" en el histórico según covariables del
     paciente actual.
  2. Para cada vacuna: contar (k = éxitos, n = ensayos) dentro de ese
     grupo similar.
  3. Posterior Beta(alpha0 + k, beta0 + n - k)  ->  E[p], var[p], IC95%.
  4. Chi-cuadrado: valida si el grupo "similar" es razonablemente
     homogéneo (si no, avisa que la estratificación puede ser floja).
  5. Orden óptimo = E[p] decreciente (lema de intercambio).
  6. P(p_i > p_j) para pares con intervalos que se solapan (Monte Carlo
     sobre las posteriores Beta, cerrado y determinístico con seed fija).
  7. E[N | orden] = dosis esperadas siguiendo el orden óptimo, y
     comparación contra el peor orden posible (para mostrarle al
     médico el "ahorro" del ordenamiento sugerido).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from dataclasses import dataclass, field

# Prior no informativo (Beta uniforme). Si el equipo médico tiene una
# creencia previa razonable (ej. de la literatura), esto se puede
# ajustar por vacuna.
ALPHA0, BETA0 = 1.0, 1.0

RNG_SEED = 12345  # fija para que P(pi>pj) sea reproducible entre corridas


@dataclass
class ResultadoVacuna:
    vacuna: str
    k: int              # éxitos observados en el grupo similar
    n: int               # ensayos observados en el grupo similar
    p_mean: float         # E[p | datos]
    ci_low: float          # IC 95% inferior
    ci_high: float          # IC 95% superior
    n_total_historico: int   # cuántos registros tenía la vacuna en TODO el histórico (sin filtrar)


@dataclass
class ResultadoPipeline:
    caso: dict
    n_similares: int
    resultados: list[ResultadoVacuna] = field(default_factory=list)
    orden_optimo: list[str] = field(default_factory=list)
    en_orden_optimo: float = 0.0
    en_peor_orden: float = 0.0
    prob_pares: pd.DataFrame = None
    alerta_estratificacion: str | None = None


def filtrar_similares(df: pd.DataFrame, caso: dict, margen_edad: int = 10) -> pd.DataFrame:
    """Filtra el histórico a los casos 'similares' al paciente actual.

    Criterios de similitud (todos opcionales salvo edad/comorbilidad,
    que son la base). Se van agregando en capas: si un criterio no
    viene en `caso`, simplemente no se aplica.

      - edad          (obligatorio): dentro de +/- margen_edad años
      - comorbilidad   (obligatorio): match exacto (0/1)
      - vacunas_previas (opcional): pacientes que ya probaron (y
        fallaron) exactamente ese mismo conjunto de vacunas antes.
        Esto importa porque la probabilidad de éxito de la próxima
        vacuna puede no ser independiente de qué se probó antes
        (ej. algo en común entre las que fallaron).
      - laboratorio_excluir (opcional): si el paciente ya tuvo una
        reacción adversa a un laboratorio y hay que descartarlo,
        se filtra ANTES de llegar acá (a nivel de qué vacunas
        candidatas se le muestran al médico), no en este filtro de
        similitud histórica.

    NOTA IMPORTANTE: este filtro por "capas" (edad + comorbilidad +
    vacunas ya probadas) reduce rápido el tamaño de muestra a medida
    que agregás criterios -- con pocos cientos de filas históricas,
    agregar "vacunas_previas" puede dejarte con muestras muy chicas
    (mirá la alerta de chi-cuadrado / n bajo en el resultado). Cuando
    tengan el volumen real de datos, si el n por celda queda chico,
    conviene migrar de "estratificar por capas" a un modelo de
    regresión logística sobre las covariables (que interpola en vez
    de exigir match exacto). Lo dejamos anotado para cuando lleguen
    los datos reales.
    """
    mask = (
        (df["comorbilidad"] == caso["comorbilidad"])
        & (df["edad"].between(caso["edad"] - margen_edad, caso["edad"] + margen_edad))
    )

    vacunas_previas = caso.get("vacunas_previas")
    if vacunas_previas:
        # pacientes cuyo conjunto de vacunas YA probadas (antes de la
        # dosis en cuestión) coincide con el del paciente actual
        vacunas_por_paciente = (
            df.sort_values("nro_dosis_en_tratamiento")
              .groupby("paciente_id")["vacuna"]
              .apply(lambda s: set(s.iloc[:len(vacunas_previas)]))
        )
        pacientes_similares = vacunas_por_paciente[
            vacunas_por_paciente == set(vacunas_previas)
        ].index
        mask &= df["paciente_id"].isin(pacientes_similares)

    return df[mask].copy()


def estimar_beta_por_vacuna(df_similares: pd.DataFrame, df_total: pd.DataFrame) -> list[ResultadoVacuna]:
    resultados = []
    for vacuna, grupo in df_similares.groupby("vacuna"):
        n = len(grupo)
        k = int(grupo["resultado"].sum())
        a_post = ALPHA0 + k
        b_post = BETA0 + (n - k)
        p_mean = a_post / (a_post + b_post)
        ci_low, ci_high = stats.beta.ppf([0.025, 0.975], a_post, b_post)
        n_total = int((df_total["vacuna"] == vacuna).sum())
        resultados.append(ResultadoVacuna(
            vacuna=vacuna, k=k, n=n, p_mean=p_mean,
            ci_low=ci_low, ci_high=ci_high, n_total_historico=n_total,
        ))
    return sorted(resultados, key=lambda r: r.p_mean, reverse=True)


def chi_cuadrado_estratificacion(df_similares: pd.DataFrame) -> tuple[float, float] | None:
    """Chequea si, dentro del grupo 'similar', la tasa de éxito por
    vacuna difiere de forma consistente con el conteo esperado
    (goodness-of-fit simple sobre la tabla vacuna x resultado).
    Devuelve (estadistico, p_valor) o None si no hay datos suficientes.
    """
    tabla = pd.crosstab(df_similares["vacuna"], df_similares["resultado"])
    if tabla.shape[0] < 2 or (tabla.values.sum(axis=1) < 5).any():
        return None
    chi2, p, _, _ = stats.chi2_contingency(tabla)
    return chi2, p


def esperanza_dosis(orden: list[str], p_por_vacuna: dict[str, float]) -> float:
    """E[N | orden] = suma de P(N >= j) = suma de productos de
    (1 - p) de las vacunas anteriores en la secuencia.
    """
    en = 0.0
    prod_fallos_previos = 1.0
    for vacuna in orden:
        en += prod_fallos_previos
        prod_fallos_previos *= (1 - p_por_vacuna[vacuna])
    return en


def prob_pares_montecarlo(resultados: list[ResultadoVacuna], n_sims: int = 200_000) -> pd.DataFrame:
    """P(p_i > p_j) para cada par de vacunas, muestreando de sus
    posteriores Beta. Determinístico (seed fija) para auditar.
    """
    rng = np.random.default_rng(RNG_SEED)
    muestras = {
        r.vacuna: rng.beta(ALPHA0 + r.k, BETA0 + (r.n - r.k), size=n_sims)
        for r in resultados
    }
    filas = []
    vacunas = [r.vacuna for r in resultados]
    for i, vi in enumerate(vacunas):
        for vj in vacunas[i + 1:]:
            prob = float(np.mean(muestras[vi] > muestras[vj]))
            filas.append({"vacuna_A": vi, "vacuna_B": vj, "P(A > B)": round(prob, 3)})
    return pd.DataFrame(filas)


def correr_pipeline(path_excel: str, caso: dict, margen_edad: int = 10) -> ResultadoPipeline:
    df = pd.read_excel(path_excel)

    columnas_esperadas = {"paciente_id", "edad", "comorbilidad", "laboratorio",
                           "vacuna", "nro_dosis_en_tratamiento", "resultado"}
    faltantes = columnas_esperadas - set(df.columns)
    if faltantes:
        raise ValueError(f"Faltan columnas en el Excel: {faltantes}")

    df_similares = filtrar_similares(df, caso, margen_edad)

    if len(df_similares) == 0:
        raise ValueError("No hay casos similares en el histórico con ese criterio de filtro.")

    resultados = estimar_beta_por_vacuna(df_similares, df)
    orden_optimo = [r.vacuna for r in resultados]  # ya viene ordenado desc por p_mean
    p_por_vacuna = {r.vacuna: r.p_mean for r in resultados}

    en_optimo = esperanza_dosis(orden_optimo, p_por_vacuna)
    en_peor = esperanza_dosis(list(reversed(orden_optimo)), p_por_vacuna)

    prob_pares = prob_pares_montecarlo(resultados)

    chi_resultado = chi_cuadrado_estratificacion(df_similares)
    alerta = None
    if chi_resultado is None:
        alerta = ("Muestra insuficiente en el grupo similar para validar la "
                   "estratificación con chi-cuadrado (se recomienda ampliar el "
                   "margen de edad o revisar manualmente).")
    else:
        _, p_valor = chi_resultado
        if p_valor < 0.05:
            alerta = (f"El test chi-cuadrado (p={p_valor:.3f}) sugiere que las tasas "
                       f"de éxito difieren significativamente dentro del grupo "
                       f"'similar' — la estratificación podría estar mezclando "
                       f"subpoblaciones distintas.")

    return ResultadoPipeline(
        caso=caso,
        n_similares=len(df_similares["paciente_id"].unique()),
        resultados=resultados,
        orden_optimo=orden_optimo,
        en_orden_optimo=en_optimo,
        en_peor_orden=en_peor,
        prob_pares=prob_pares,
        alerta_estratificacion=alerta,
    )


def resultado_a_dict(resultado: ResultadoPipeline) -> dict:
    """Serializa un ResultadoPipeline al mismo JSON que devuelve el
    endpoint POST /calcular-orden de api.py.

    Se centraliza acá (en vez de duplicar el armado del dict en
    api.py y en generar_reporte.py) para garantizar que el LLM que
    redacta el resumen ejecutivo del reporte reciba EXACTAMENTE el
    mismo JSON agregado que ya expone y audita el endpoint -- nunca
    una variante distinta con más o menos datos.
    """
    return {
        "n_similares": resultado.n_similares,
        "orden_sugerido": resultado.orden_optimo,
        "dosis_esperadas_orden_optimo": round(resultado.en_orden_optimo, 3),
        "dosis_esperadas_peor_orden": round(resultado.en_peor_orden, 3),
        "vacunas": [
            {
                "vacuna": r.vacuna, "p_estimado": round(r.p_mean, 3),
                "ic95": [round(r.ci_low, 3), round(r.ci_high, 3)],
                "k": r.k, "n": r.n, "n_historico_total": r.n_total_historico,
            }
            for r in resultado.resultados
        ],
        "comparaciones_pareadas": resultado.prob_pares.to_dict(orient="records"),
        "alerta_estratificacion": resultado.alerta_estratificacion,
    }


if __name__ == "__main__":
    caso_ejemplo = {"edad": 30, "comorbilidad": 0}
    resultado = correr_pipeline(
        "/home/claude/vaccine_project/historico_vacunas_SINTETICO.xlsx",
        caso_ejemplo,
    )

    print(f"Caso: {resultado.caso}")
    print(f"Casos similares encontrados en histórico: {resultado.n_similares}")
    print()
    print("Estimaciones por vacuna (ordenadas por E[p] desc):")
    for r in resultado.resultados:
        print(f"  {r.vacuna:10s}  E[p]={r.p_mean:.3f}  IC95%=[{r.ci_low:.3f}, {r.ci_high:.3f}]  "
              f"(k={r.k}, n={r.n} en grupo similar; n={r.n_total_historico} en histórico total)")
    print()
    print(f"Orden óptimo sugerido: {' -> '.join(resultado.orden_optimo)}")
    print(f"E[dosis] con orden óptimo: {resultado.en_orden_optimo:.3f}")
    print(f"E[dosis] con el peor orden posible: {resultado.en_peor_orden:.3f}")
    print(f"Ahorro esperado: {resultado.en_peor_orden - resultado.en_orden_optimo:.3f} dosis")
    print()
    print("Probabilidades pareadas P(A > B):")
    print(resultado.prob_pares.to_string(index=False))
    if resultado.alerta_estratificacion:
        print()
        print(f"ALERTA: {resultado.alerta_estratificacion}")
