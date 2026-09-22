"""
Genera un reporte HTML autocontenido (tabla + gráficos) a partir del
resultado del motor_probabilidades.py. Pensado para que un médico lo
abra directo en el navegador (o se convierta a PDF con Ctrl+P).

El LLM, si se usa en producción, entraría acá SOLO para redactar el
párrafo de "resumen en lenguaje natural" al principio -- nunca para
generar los números de la tabla o los gráficos, que sos calculados
100% por motor_probabilidades.py.
"""
import base64
import io
import subprocess
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from motor_probabilidades import correr_pipeline, ResultadoPipeline


def _fig_a_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def grafico_probabilidades(resultado: ResultadoPipeline) -> str:
    vacunas = [r.vacuna for r in resultado.resultados]
    medias = [r.p_mean for r in resultado.resultados]
    err_low = [r.p_mean - r.ci_low for r in resultado.resultados]
    err_high = [r.ci_high - r.p_mean for r in resultado.resultados]

    fig, ax = plt.subplots(figsize=(7, 4))
    colores = ["#2e7d32" if i == 0 else "#5c6bc0" for i in range(len(vacunas))]
    ax.bar(vacunas, medias, yerr=[err_low, err_high], capsize=6, color=colores)
    ax.set_ylabel("Probabilidad estimada de éxito")
    ax.set_ylim(0, 1)
    ax.set_title("Probabilidad de éxito por vacuna (con IC 95%)")
    for i, m in enumerate(medias):
        ax.text(i, m + 0.03, f"{m:.2f}", ha="center", fontsize=9)
    return _fig_a_base64(fig)


def grafico_ahorro(resultado: ResultadoPipeline) -> str:
    fig, ax = plt.subplots(figsize=(5, 4))
    etiquetas = ["Orden sugerido", "Peor orden posible"]
    valores = [resultado.en_orden_optimo, resultado.en_peor_orden]
    ax.bar(etiquetas, valores, color=["#2e7d32", "#c62828"])
    ax.set_ylabel("Dosis esperadas E[N]")
    ax.set_title("Impacto del ordenamiento en dosis esperadas")
    for i, v in enumerate(valores):
        ax.text(i, v + 0.03, f"{v:.2f}", ha="center", fontsize=9)
    return _fig_a_base64(fig)


def generar_html(resultado: ResultadoPipeline, path_salida: str):
    img_prob = grafico_probabilidades(resultado)
    img_ahorro = grafico_ahorro(resultado)

    filas_tabla = "\n".join(
        f"<tr><td>{i+1}</td><td>{r.vacuna}</td>"
        f"<td>{r.p_mean:.1%}</td>"
        f"<td>[{r.ci_low:.1%} - {r.ci_high:.1%}]</td>"
        f"<td>{r.k}/{r.n}</td>"
        f"<td>{r.n_total_historico}</td></tr>"
        for i, r in enumerate(resultado.resultados)
    )

    filas_pares = "\n".join(
        f"<tr><td>{row.vacuna_A}</td><td>{row.vacuna_B}</td>"
        f"<td>{row['P(A > B)']:.1%}</td></tr>"
        for _, row in resultado.prob_pares.iterrows()
    )

    alerta_html = ""
    if resultado.alerta_estratificacion:
        alerta_html = f"""
        <div class="alerta">
            ⚠️ <strong>Nota de validez estadística:</strong> {resultado.alerta_estratificacion}
        </div>
        """

    ahorro = resultado.en_peor_orden - resultado.en_orden_optimo

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
    <meta charset="UTF-8">
    <title>Reporte de orden de vacunación sugerido</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; color: #222; }}
        h1 {{ font-size: 22px; border-bottom: 2px solid #333; padding-bottom: 8px; }}
        h2 {{ font-size: 17px; margin-top: 30px; color: #333; }}
        table {{ border-collapse: collapse; width: auto; margin: 12px 0; }}
        th, td {{ border: 1px solid #ccc; padding: 6px 14px; text-align: center; font-size: 12px; white-space: nowrap; }}
        th {{ background: #f0f0f0; }}
        tr:first-child td {{ background: #eaf4ea; font-weight: bold; }}
        .alerta {{ background: #fff3cd; border: 1px solid #ffe08a; padding: 12px; border-radius: 6px; margin: 16px 0; font-size: 14px; }}
        .disclaimer {{ background: #eef2f7; border-left: 4px solid #5c6bc0; padding: 12px 16px; font-size: 13px; margin-top: 30px; }}
        .meta {{ color: #666; font-size: 13px; }}
        img {{ max-width: 100%; }}
        .orden {{ font-size: 16px; background: #eaf4ea; padding: 12px; border-radius: 6px; font-weight: bold; }}
    </style>
    </head>
    <body>
        <h1>Reporte de soporte a decisión — Orden sugerido de vacunación</h1>
        <p class="meta">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')} |
        Caso: edad {resultado.caso['edad']} años, comorbilidad {'sí' if resultado.caso['comorbilidad'] else 'no'} |
        Casos históricos similares utilizados: {resultado.n_similares}</p>

        {alerta_html}

        <h2>Orden sugerido</h2>
        <div class="orden">{' → '.join(resultado.orden_optimo)}</div>
        <p>Dosis esperadas siguiendo este orden: <strong>{resultado.en_orden_optimo:.2f}</strong>
        &nbsp;|&nbsp; Dosis esperadas en el peor orden posible: <strong>{resultado.en_peor_orden:.2f}</strong>
        &nbsp;|&nbsp; Ahorro esperado: <strong>{ahorro:.2f} dosis</strong></p>

        <h2>Probabilidad de éxito estimada por vacuna</h2>
        <img src="data:image/png;base64,{img_prob}">
        <table>
            <tr><th>#</th><th>Vacuna</th><th>E[p]</th><th>IC 95%</th>
                <th>Éxitos/Casos similares</th><th>N histórico total</th></tr>
            {filas_tabla}
        </table>

        <h2>Impacto del ordenamiento</h2>
        <img src="data:image/png;base64,{img_ahorro}">

        <h2>Comparaciones pareadas P(vacuna A &gt; vacuna B)</h2>
        <p style="font-size:13px; color:#555;">Útil cuando los intervalos de dos vacunas se solapan:
        indica qué tan seguro está el modelo de que una es mejor que la otra.</p>
        <table>
            <tr><th>Vacuna A</th><th>Vacuna B</th><th>P(A &gt; B)</th></tr>
            {filas_pares}
        </table>

        <div class="disclaimer">
        Este reporte es una <strong>recomendación estadística</strong> basada en datos históricos,
        calculada para minimizar la cantidad esperada de dosis. No reemplaza el criterio clínico:
        la decisión final de orden y elección de vacunas queda a cargo del médico tratante,
        considerando factores individuales del paciente no capturados en este modelo.
        </div>
    </body>
    </html>
    """

    with open(path_salida, "w", encoding="utf-8") as f:
        f.write(html)


def convertir_a_pdf(path_html: str) -> str:
    """PDF vía wkhtmltopdf -- respeta el CSS (colores, anchos de
    tabla, etc.) mucho mejor que el conversor de LibreOffice."""
    path_pdf = str(Path(path_html).with_suffix(".pdf"))
    subprocess.run(
        ["wkhtmltopdf", "--enable-local-file-access", path_html, path_pdf],
        check=True, capture_output=True, timeout=60,
    )
    return path_pdf


def convertir_a_docx(path_html: str) -> str:
    """Word vía LibreOffice headless. El resultado es editable pero
    no respeta el CSS con la misma fidelidad que el PDF -- pensado
    como punto de partida para que el equipo lo retoque, no como
    versión final."""
    carpeta = str(Path(path_html).parent)
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "docx:MS Word 2007 XML",
         "--outdir", carpeta, path_html],
        check=True, capture_output=True, timeout=60,
    )
    return str(Path(path_html).with_suffix(".docx"))


if __name__ == "__main__":
    caso_ejemplo = {
        "edad": 30,
        "comorbilidad": 0,
        # ejemplo de uso del nuevo criterio de similitud opcional:
        # "vacunas_previas": ["VacunaC"],
    }
    resultado = correr_pipeline(
        "/home/claude/vaccine_project/historico_vacunas_SINTETICO.xlsx",
        caso_ejemplo,
    )
    path_html = "/home/claude/vaccine_project/reporte_ejemplo.html"
    generar_html(resultado, path_html)
    print("Reporte HTML generado.")

    path_pdf = convertir_a_pdf(path_html)
    print(f"Reporte PDF generado: {path_pdf}")

    path_docx = convertir_a_docx(path_html)
    print(f"Reporte Word generado: {path_docx}")
