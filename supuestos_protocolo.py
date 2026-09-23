"""
supuestos_protocolo.py

ÚNICA fuente de verdad de los supuestos clínicos del protocolo
treat-and-extend (T&E) para inyecciones intravítreas anti-VEGF.

La usan, con exactamente las mismas reglas:
  - generar_datos_rtu.py  (para SIMULAR el histórico)
  - el motor de Markov    (fase 2, para MODELAR el tratamiento)

Así se garantiza que se simula y se modela con el mismo protocolo, y
cuando un clínico corrija un valor ("acá se extiende de a 4 semanas")
se cambia en UN solo lugar.

IMPORTANTE: los valores por defecto son SUPUESTOS DE SIMULACIÓN
inspirados en el esquema T&E típico descrito en la literatura. NO
están validados por un clínico ni corresponden a un protocolo concreto
(NICE / Moorfields tienen variantes por patología). Deben revisarse
antes de usar datos reales.

Semántica de una visita (en este orden):
  1. se evalúa la actividad de la enfermedad (con los observables del día)
  2. se aplica la regla de transición -> acción
  3. se inyecta (salvo acciones absorbentes: "switch", "estable")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Fase = Literal["carga", "mantenimiento"]

ACCION_CARGA = "inyectar_carga"
ACCION_EXTENDER = "inyectar_extender"
ACCION_MANTENER = "inyectar_mantener"
ACCION_ACORTAR = "inyectar_acortar"
ACCION_SWITCH = "switch"      # absorbente para el ciclo del fármaco actual
ACCION_ESTABLE = "estable"    # absorbente: se suspende el tratamiento

ACCIONES_ABSORBENTES = {ACCION_SWITCH, ACCION_ESTABLE}


@dataclass(frozen=True)
class SupuestosProtocolo:
    # --- Fase de carga ---
    dosis_carga: int = 3
    intervalo_carga_semanas: int = 4

    # --- Mantenimiento treat-and-extend ---
    paso_extension_semanas: int = 2
    paso_acortamiento_semanas: int = 2
    intervalo_min_semanas: int = 4
    intervalo_max_semanas: int = 16

    # --- Estados absorbentes ---
    visitas_activas_en_min_para_switch: int = 3
    visitas_secas_en_max_para_estabilidad: int = 3

    # --- Abandono (por visita programada) ---
    prob_abandono_por_visita: float = 0.02

    # --- Definición operativa de "enfermedad activa" ---
    # activo si: IRF presente, o SRF presente, o el CMT sube más que
    # este umbral respecto de la visita previa, o la AV (decimal) cae
    # más que este umbral respecto de la visita previa.
    umbral_aumento_cmt_um: float = 50.0
    umbral_caida_av_decimal: float = 0.10

    def __post_init__(self):
        if self.dosis_carga < 1:
            raise ValueError("dosis_carga debe ser >= 1")
        if not (0 < self.intervalo_min_semanas <= self.intervalo_max_semanas):
            raise ValueError("Se requiere 0 < intervalo_min <= intervalo_max")
        if not (self.intervalo_min_semanas <= self.intervalo_carga_semanas <= self.intervalo_max_semanas):
            raise ValueError("intervalo_carga debe estar entre intervalo_min e intervalo_max")
        if self.paso_extension_semanas <= 0 or self.paso_acortamiento_semanas <= 0:
            raise ValueError("Los pasos de extensión/acortamiento deben ser positivos")
        if not (0 <= self.prob_abandono_por_visita < 1):
            raise ValueError("prob_abandono_por_visita debe estar en [0, 1)")

    def intervalos_alcanzables(self) -> list[int]:
        """Intervalos de mantenimiento que efectivamente se pueden
        alcanzar desde el intervalo de carga aplicando extender/acortar
        (con topes). Los usa el motor de Markov para enumerar estados."""
        vistos = {self.intervalo_carga_semanas}
        pendientes = [self.intervalo_carga_semanas]
        while pendientes:
            q = pendientes.pop()
            for q2 in (
                min(q + self.paso_extension_semanas, self.intervalo_max_semanas),
                max(q - self.paso_acortamiento_semanas, self.intervalo_min_semanas),
            ):
                if q2 not in vistos:
                    vistos.add(q2)
                    pendientes.append(q2)
        return sorted(vistos)


SUPUESTOS_DEFAULT = SupuestosProtocolo()


@dataclass(frozen=True)
class EstadoCiclo:
    """Situación de un ojo con UN fármaco al llegar a una visita.

    - fase "carga": dosis_carga_previas = inyecciones de carga ya
      aplicadas antes de esta visita (0 .. dosis_carga-1).
    - fase "mantenimiento": intervalo_semanas = intervalo que acaba de
      transcurrir; contadores de visitas consecutivas activas en el
      intervalo mínimo / secas en el máximo (necesarios para que el
      proceso sea markoviano: la regla depende de ellos).
    """
    fase: Fase
    dosis_carga_previas: int = 0
    intervalo_semanas: int = 0
    activas_consecutivas_en_min: int = 0
    secas_consecutivas_en_max: int = 0

    @staticmethod
    def inicio() -> "EstadoCiclo":
        return EstadoCiclo(fase="carga", dosis_carga_previas=0)

    def etiqueta(self) -> str:
        if self.fase == "carga":
            return f"C{self.dosis_carga_previas + 1}"
        base = f"M{self.intervalo_semanas}"
        if self.activas_consecutivas_en_min:
            base += f"_a{self.activas_consecutivas_en_min}"
        if self.secas_consecutivas_en_max:
            base += f"_s{self.secas_consecutivas_en_max}"
        return base


@dataclass(frozen=True)
class Decision:
    accion: str
    inyecta: bool
    siguiente: EstadoCiclo | None        # None si la acción es absorbente
    intervalo_hasta_proxima: int | None  # None si la acción es absorbente


def transicion(estado: EstadoCiclo, activo: bool,
               sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> Decision:
    """Regla determinística del protocolo T&E. Dado el estado al llegar
    a la visita y si la enfermedad se evaluó como activa, devuelve la
    acción y el estado siguiente. En fase de carga la actividad NO
    cambia la decisión (se completan las dosis de carga igual)."""
    if estado.fase == "carga":
        aplicadas = estado.dosis_carga_previas + 1
        if aplicadas < sup.dosis_carga:
            siguiente = EstadoCiclo(fase="carga", dosis_carga_previas=aplicadas)
        else:
            siguiente = EstadoCiclo(fase="mantenimiento",
                                    intervalo_semanas=sup.intervalo_carga_semanas)
        return Decision(ACCION_CARGA, True, siguiente, sup.intervalo_carga_semanas)

    q = estado.intervalo_semanas

    if activo:
        activas = estado.activas_consecutivas_en_min + 1 if q == sup.intervalo_min_semanas else 0
        if activas >= sup.visitas_activas_en_min_para_switch:
            return Decision(ACCION_SWITCH, False, None, None)
        q2 = max(q - sup.paso_acortamiento_semanas, sup.intervalo_min_semanas)
        accion = ACCION_ACORTAR if q2 < q else ACCION_MANTENER
        siguiente = EstadoCiclo(fase="mantenimiento", intervalo_semanas=q2,
                                activas_consecutivas_en_min=activas)
        return Decision(accion, True, siguiente, q2)

    secas = estado.secas_consecutivas_en_max + 1 if q == sup.intervalo_max_semanas else 0
    if secas >= sup.visitas_secas_en_max_para_estabilidad:
        return Decision(ACCION_ESTABLE, False, None, None)
    q2 = min(q + sup.paso_extension_semanas, sup.intervalo_max_semanas)
    accion = ACCION_EXTENDER if q2 > q else ACCION_MANTENER
    siguiente = EstadoCiclo(fase="mantenimiento", intervalo_semanas=q2,
                            secas_consecutivas_en_max=secas)
    return Decision(accion, True, siguiente, q2)


def evaluar_actividad(irf: bool, srf: bool,
                      cmt: float, cmt_previo: float | None,
                      av: float, av_previa: float | None,
                      sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> bool:
    """Definición operativa de enfermedad activa (ver SupuestosProtocolo).
    En la primera visita (sin valores previos) solo cuentan IRF/SRF."""
    if irf or srf:
        return True
    if cmt_previo is not None and cmt - cmt_previo > sup.umbral_aumento_cmt_um:
        return True
    if av_previa is not None and av_previa - av > sup.umbral_caida_av_decimal:
        return True
    return False
