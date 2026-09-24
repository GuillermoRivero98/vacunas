"""
servicio_rtu.py  --  Fase 5 (T5.4, T5.5)

Capa de servicio entre la API y los módulos RTU:

  - Mantiene en memoria el histórico, los estimadores entrenados y el
    índice de casos similares. NUNCA entrena por request: entrena la
    primera vez que se lo necesita y cada vez que el histórico cambia
    (se detecta por el ETag del objeto en R2).
  - Arma la respuesta de /rtu/sugerir-plan: recomendación vía Markov,
    métricas por fármaco, explicación por casos similares, supuestos y
    advertencias.

La fuente del histórico se inyecta (`cargador`), así la misma lógica se
prueba en local con un archivo y en producción con R2.
"""
from __future__ import annotations

import dataclasses
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from esquema_rtu import leer_y_validar
from estimacion_rtu import (
    EstimadorRedBayesiana,
    recomendar_por_linea,
)
# from estimacion_rtu import EstimadorBeta   # Camino A desactivado (ADR-16)
from explicacion_rtu import TIEMPO_DISCREPANCIA, IndiceCasos
import compras_rtu
from supuestos_protocolo import SUPUESTOS_DEFAULT

# () -> (etag, fuente, formato). etag es None si no hay histórico
# disponible; fuente puede ser un path, un file-like o una función que lo
# devuelva (descarga perezosa); formato es "xlsx" o "csv".
Cargador = Callable[[], tuple[str | None, object, str]]

ADVERTENCIA_SUPUESTOS = ("Los supuestos del protocolo (intervalos, criterios de switch y "
                         "estabilidad, abandono) no están validados clínicamente.")
ADVERTENCIA_SIMULADOS = "Los resultados se basan en datos SIMULADOS, no en pacientes reales."
ADVERTENCIA_MEDICO = "La decisión final es del médico tratante."


COMPRA_POR_DEFECTO = {"horizonte_semanas": 52, "nivel_servicio": 0.95, "nuevos_ojos_por_semana": 0.0}


def _clave_compra(horizonte_semanas: int, nivel_servicio: float, nuevos_ojos_por_semana: float) -> tuple:
    return (int(horizonte_semanas), round(float(nivel_servicio), 4), round(float(nuevos_ojos_por_semana), 4))


class HistoricoNoDisponible(RuntimeError):
    pass


class SolicitudInvalida(ValueError):
    pass


@dataclass
class _Modelo:
    etag: str
    entrenado_en: str
    farmacos: list[str]
    n_pacientes: int
    n_ojos: int
    n_visitas: int
    estimadores: dict
    indice: IndiceCasos
    datos: pd.DataFrame
    compras: dict = dataclasses.field(default_factory=dict)       # (H, nivel, lambda) -> resultado
    calibraciones: dict = dataclasses.field(default_factory=dict)  # H -> calibración


class ServicioRTU:
    def __init__(self, cargador: Cargador, datos_simulados: bool = True):
        self._cargador = cargador
        self._datos_simulados = datos_simulados
        self._modelo: _Modelo | None = None
        self._lock = threading.Lock()
        self._entrenando = False
        self._ultimo_error: str | None = None
        self._hilo: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Estado del modelo
    # ------------------------------------------------------------------

    def invalidar(self) -> None:
        with self._lock:
            self._modelo = None

    def estado(self) -> dict:
        m = self._modelo
        return {
            "rtu_modelo_entrenado": m is not None,
            "rtu_compra_precalculada": m is not None and _clave_compra(**COMPRA_POR_DEFECTO) in m.compras,
            "rtu_entrenando": self._entrenando,
            "rtu_version_modelo": None if m is None else {"etag": m.etag, "entrenado_en": m.entrenado_en},
            "rtu_ultimo_error": self._ultimo_error,
        }

    def precalentar_en_segundo_plano(self) -> None:
        """Entrena en un hilo aparte, sin bloquear el request que lo
        dispara. Si ya hay un entrenamiento en curso, no lanza otro. Si
        no hay histórico, no hace nada.

        NO se llama al arrancar el servicio: en el plan gratuito de Render,
        entrenar durante el arranque (aun en otro hilo, por el GIL de
        Python) impidió que el servidor abriera su puerto a tiempo y el
        deploy falló por "port scan timeout". Se dispara desde /health y
        después de cada carga de histórico, con el servidor ya escuchando."""
        if self._hilo is not None and self._hilo.is_alive():
            return

        def tarea():
            try:
                self.modelo()
                # Precalcula la estimación de compra por defecto (la más lenta:
                # incluye los backtests de calibración).
                self.estimar_compra(**COMPRA_POR_DEFECTO)
                self._ultimo_error = None
            except HistoricoNoDisponible:
                pass
            except Exception as e:  # queda visible en /health
                self._ultimo_error = f"{type(e).__name__}: {e}"
        self._hilo = threading.Thread(target=tarea, name="precalentar-rtu", daemon=True)
        self._hilo.start()

    def _entrenar(self, etag: str, fuente, formato: str) -> _Modelo:
        df = leer_y_validar(fuente, formato)
        return _Modelo(
            etag=etag,
            entrenado_en=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            farmacos=sorted(df["farmaco"].astype(str).unique()),
            n_pacientes=int(df["paciente_id"].nunique()),
            n_ojos=int(df.groupby(["paciente_id", "ojo"]).ngroups),
            n_visitas=len(df),
            estimadores={
                "red_factorizada": EstimadorRedBayesiana(df, "factorizada"),
                # "beta": EstimadorBeta(df),   # Camino A desactivado (ADR-16)
            },
            indice=IndiceCasos(df),
            datos=df,
        )

    def modelo(self) -> _Modelo:
        """Devuelve el modelo vigente; reentrena si el histórico cambió.
        Chequea el ETag en cada llamada (una consulta HEAD liviana a R2)."""
        etag, fuente, formato = self._cargador()
        if etag is None:
            raise HistoricoNoDisponible("No hay histórico RTU cargado todavía.")
        with self._lock:
            if self._modelo is None or self._modelo.etag != etag:
                self._entrenando = True
                try:
                    self._modelo = self._entrenar(etag, fuente() if callable(fuente) else fuente, formato)
                finally:
                    self._entrenando = False
            return self._modelo

    # ------------------------------------------------------------------
    # Recomendación
    # ------------------------------------------------------------------

    def sugerir_plan(self, caso: dict, farmacos_ya_probados: list[str] | None = None,
                     objetivo: str = "estable", metodo: str = "red_factorizada",
                     n_casos_similares: int = 10) -> dict:
        m = self.modelo()
        ya = list(dict.fromkeys(farmacos_ya_probados or []))
        desconocidos = [f for f in ya if f not in m.farmacos]
        if desconocidos:
            raise SolicitudInvalida(f"Fármacos desconocidos en farmacos_ya_probados: {desconocidos}. "
                                    f"Conocidos: {m.farmacos}")
        candidatos = [f for f in m.farmacos if f not in ya]
        if not candidatos:
            raise SolicitudInvalida("Ya se probaron todos los fármacos del histórico: no hay candidatos.")
        if metodo not in m.estimadores:
            raise SolicitudInvalida(f"metodo debe ser uno de {list(m.estimadores)}")
        linea = len(ya) + 1
        est = m.estimadores[metodo]

        rec = recomendar_por_linea(est, caso, candidatos, objetivo=objetivo, linea_inicial=linea)

        p_q8 = {f: est.p(caso, f, TIEMPO_DISCREPANCIA, linea) for f in candidatos}
        explicacion = m.indice.explicar(caso, candidatos, linea, n_casos_similares, p_q8)

        # Camino A desactivado (ADR-16): agregaba k/n y nivel de similitud.
        # if metodo == "beta":
        #     for f in candidatos:
        #         det = est.detalle(caso, f, TIEMPO_DISCREPANCIA, linea)
        #         explicacion["base_de_calculo"][f]["camino_A_q6_8"] = {
        #             "k_activos": det.k, "n_visitas": det.n, "nivel_similitud": det.nivel}

        por_farmaco = sorted((
            {"farmaco": f,
             "prob_estable": round(r.prob_absorcion["estable"], 4),
             "prob_switch": round(r.prob_absorcion["switch"], 4),
             "prob_abandono": round(r.prob_absorcion["abandono"], 4),
             "inyecciones_esperadas": round(r.inyecciones_esperadas, 2),
             "visitas_esperadas": round(r.visitas_esperadas, 2),
             "semanas_esperadas": round(r.semanas_esperadas, 1)}
            for f, r in rec.por_farmaco.items()),
            key=lambda x: rec.orden.index(x["farmaco"]))

        advertencias = [ADVERTENCIA_MEDICO, ADVERTENCIA_SUPUESTOS]
        if self._datos_simulados:
            advertencias.append(ADVERTENCIA_SIMULADOS)
        advertencias += explicacion.pop("advertencias")

        return {
            "metodo": metodo,
            "objetivo": objetivo,
            "linea": linea,
            "farmacos_ya_probados": ya,
            "orden_sugerido": rec.orden,
            "valor_orden": {k: round(v, 4) for k, v in rec.valor_orden.items()},
            "por_farmaco": por_farmaco,
            **explicacion,
            "supuestos": dataclasses.asdict(SUPUESTOS_DEFAULT),
            "advertencias": advertencias,
            "version_modelo": {"etag": m.etag, "entrenado_en": m.entrenado_en,
                               "pacientes": m.n_pacientes, "ojos": m.n_ojos, "visitas": m.n_visitas},
        }



    # ------------------------------------------------------------------
    # Estimación de compra (RF-21)
    # ------------------------------------------------------------------

    def estimar_compra(self, horizonte_semanas: int = 52, nivel_servicio: float = 0.95,
                       nuevos_ojos_por_semana: float = 0.0) -> dict:
        """Demanda y compra sugerida por fármaco. Cachea por versión del
        histórico: la calibración (lo más lento) se calcula una vez por
        horizonte; cambiar el nivel de servicio o la tasa de nuevos no la
        recalcula."""
        m = self.modelo()
        clave = _clave_compra(horizonte_semanas, nivel_servicio, nuevos_ojos_por_semana)
        if clave not in m.compras:
            H = int(horizonte_semanas)
            if H not in m.calibraciones:
                corte = int(m.datos["semana"].max())
                m.calibraciones[H] = compras_rtu.calibrar(m.datos, corte, H)
            r = compras_rtu.estimar_compra(m.datos, H, nivel_servicio, nuevos_ojos_por_semana,
                                           calibracion=m.calibraciones[H])
            r["advertencias"] = [ADVERTENCIA_SUPUESTOS] + ([ADVERTENCIA_SIMULADOS] if self._datos_simulados else [])
            if m.calibraciones[H].get("advertencia"):
                r["advertencias"].append(m.calibraciones[H]["advertencia"])
            r["version_modelo"] = {"etag": m.etag, "entrenado_en": m.entrenado_en}
            m.compras[clave] = r
        return m.compras[clave]


def cargador_r2() -> Cargador:
    """Cargador de producción: ETag + descarga perezosa desde R2. Usa la
    copia CSV si existe (mucho más rápida de leer); si no, el Excel
    original (compatibilidad con cargas hechas antes de existir el CSV)."""
    import almacenamiento_r2 as r2

    def cargar():
        etag_csv = r2.etag(r2.R2_OBJECT_KEY_RTU_CSV)
        if etag_csv is not None:
            return etag_csv, (lambda: r2.descargar_historico(r2.R2_OBJECT_KEY_RTU_CSV)), "csv"
        etag_xlsx = r2.etag(r2.R2_OBJECT_KEY_RTU)
        return etag_xlsx, (lambda: r2.descargar_historico(r2.R2_OBJECT_KEY_RTU)), "xlsx"
    return cargar


def cargador_archivo(path: str) -> Cargador:
    """Cargador local (tests y desarrollo): el ETag es la fecha de
    modificación del archivo."""
    def cargar():
        if not os.path.exists(path):
            return None, None, "xlsx"
        formato = "csv" if path.lower().endswith(".csv") else "xlsx"
        return f"local-{os.path.getmtime(path)}", path, formato
    return cargar


def _bool_entorno(nombre: str, default: str = "true") -> bool:
    return os.environ.get(nombre, default).strip().lower() not in ("0", "false", "no")


def datos_simulados_desde_entorno() -> bool:
    return _bool_entorno("RTU_DATOS_SIMULADOS")


def precalentar_desde_entorno() -> bool:
    return _bool_entorno("RTU_PRECALENTAR")
