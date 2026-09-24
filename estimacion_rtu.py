"""
estimacion_rtu.py  --  Fase 3

Estima p_activo(estado, paciente, fármaco): la probabilidad de que, en
una visita, la enfermedad se evalúe ACTIVA, condicionada al paciente.
Es la única entrada que necesita el motor de Markov (markov_rtu.py).

TODO ES PROBABILIDAD CLÁSICA: conteos con pandas, probabilidad
condicional y regla de Bayes. Nada de aprendizaje automático ni IA.

Método del sistema -- EstimadorRedBayesiana (grafo probabilístico)
  Tablas de probabilidad condicional con suavizado de Laplace y regla de
  Bayes (fórmulas en la clase). Si falta un dato del paciente (p.ej. no
  se sabe si fuma), ese factor se omite: es marginalizar la variable.

Método desactivado -- EstimadorBeta (Camino A)
  Comentado con "#" más abajo, sin ejecutarse (ADR-16). Queda por si se
  quiere retomar.

Línea base -- EstimadorFrecuencias
  Frecuencia poblacional sin datos del paciente. Solo para comparar en
  la evaluación offline.

Corrección del sesgo de selección: se estratifica por LÍNEA de
tratamiento (1ra vs 2da o posterior). Quien recibe un fármaco como
segunda línea ya falló con otro y responde peor en general; mezclar
líneas subestima a los fármacos que se usan más como rescate.

Variables deliberadamente NO usadas: glaucoma y cristalino (no se
espera clínicamente que modifiquen la respuesta anti-VEGF). Es una
decisión de diseño documentada, revisable con un clínico.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np
import pandas as pd

from markov_rtu import ResultadoMarkov, analizar_farmaco, orden_optimo, valor_secuencia
from supuestos_protocolo import SUPUESTOS_DEFAULT, EstadoCiclo, SupuestosProtocolo

# ---------------------------------------------------------------------------
# Discretizaciones compartidas por ambos caminos
# ---------------------------------------------------------------------------

TIEMPOS = ["C1", "C2", "C3", "q4", "q6-8", "q10-12", "q14-16"]
COMORBILIDADES = ["diabetes", "hipertension", "acv_iam_reciente", "tabaquismo"]


def tiempo_de_intervalo(q: int) -> str:
    if q <= 4:
        return "q4"
    if q <= 8:
        return "q6-8"
    if q <= 12:
        return "q10-12"
    return "q14-16"


def tiempo_de_estado(e: EstadoCiclo) -> str:
    if e.fase == "carga":
        return f"C{e.dosis_carga_previas + 1}"
    return tiempo_de_intervalo(e.intervalo_semanas)


def tiempo_de_etiqueta(etiqueta: str, intervalo_transcurrido: int) -> str:
    m = re.match(r"^C(\d+)", etiqueta)
    return f"C{m.group(1)}" if m else tiempo_de_intervalo(int(intervalo_transcurrido))


def subtipo(diagnostico: str, tipo_mnv: str | None) -> str:
    if diagnostico == "DMRE" and tipo_mnv and tipo_mnv != "NA":
        return f"DMRE-{tipo_mnv}"
    return diagnostico


def linea_cat(linea: int) -> str:
    return "1" if int(linea) == 1 else "2+"


def edad_cat(edad: float) -> str:
    return "<65" if edad < 65 else ("65-79" if edad < 80 else "80+")


def carga_cat(conteo: int) -> str:
    return "0" if conteo == 0 else ("1" if conteo == 1 else "2+")


def preparar(historico: pd.DataFrame) -> pd.DataFrame:
    """Columnas derivadas comunes, a partir del esquema de generar_datos_rtu."""
    df = pd.DataFrame({
        "paciente_id": historico["paciente_id"].values,
        "ojo": historico["ojo"].values,
        "farmaco": historico["farmaco"].astype(str).values,
        "subtipo": [subtipo(d, m) for d, m in zip(historico["diagnostico"], historico["tipo_mnv"].astype(str))],
        "edad": historico["edad"].values,
        "tiempo": [tiempo_de_etiqueta(e, q) for e, q in
                   zip(historico["estado"], historico["intervalo_transcurrido_semanas"].fillna(0))],
        "linea": [linea_cat(l) for l in historico["nro_farmaco_en_secuencia"]],
        "carga_comorbida": [carga_cat(int(c)) for c in historico[COMORBILIDADES].sum(axis=1)],
        "activo": historico["activo"].astype(int).values,
    })
    df["edad_cat"] = [edad_cat(e) for e in df["edad"]]
    return df


class Estimador(Protocol):
    nombre: str
    def p(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> float: ...


# ===========================================================================
# CAMINO A -- DESACTIVADO (2026-09-24, ADR-16)
# ---------------------------------------------------------------------------
# Decisión: el sistema usa un solo método, el grafo probabilístico
# (EstimadorRedBayesiana, más abajo). El Camino A (posterior Beta sobre
# "casos similares" filtrados a mano) queda comentado, sin ejecutarse,
# por si se quiere retomar.
#
# Resultados que tenía (README 12.3 y 12.4): Brier 0.2335 en visitas;
# 26.7 inyecciones por ojo en la evaluación de políticas (el grafo: 25.3).
#
# Para reactivarlo: quitar el "# " de cada línea de este bloque y volver a
# agregar "beta" como método en servicio_rtu.py y en CasoRTU (api.py).
# ===========================================================================
# # ---------------------------------------------------------------------------
# # Camino A -- Beta posterior sobre visitas similares
# # ---------------------------------------------------------------------------
#
# @dataclass
# class DetalleBeta:
#     p_mean: float
#     k: int
#     n: int
#     nivel: str
#
#
# class EstimadorBeta:
#     """Niveles de similitud (se usa el primero con n >= n_min):
#        1. subtipo + edad±margen + tiempo + fármaco + línea
#        2. subtipo + tiempo + fármaco + línea          (se relaja edad)
#        3. tiempo + fármaco + línea                     (se relaja subtipo)
#        4. tiempo + fármaco                             (último recurso)
#     Con usar_covariables=False salta directo al nivel 3 (o 4 si
#     estratificar_linea=False): sirve de línea base poblacional."""
#
#     def __init__(self, historico: pd.DataFrame, margen_edad: int = 10, n_min: int = 30,
#                  alpha0: float = 1.0, beta0: float = 1.0,
#                  usar_covariables: bool = True, estratificar_linea: bool = True):
#         self.nombre = "A_beta" + ("" if usar_covariables else "_poblacional") + ("" if estratificar_linea else "_sin_linea")
#         self.margen_edad, self.n_min = margen_edad, n_min
#         self.a0, self.b0 = alpha0, beta0
#         self.usar_covariables, self.estratificar_linea = usar_covariables, estratificar_linea
#         df = preparar(historico)
#         if not estratificar_linea:
#             df["linea"] = "todas"
#         self._g4 = {k: g["activo"].to_numpy() for k, g in df.groupby(["subtipo", "tiempo", "farmaco", "linea"])}
#         self._g4_edad = {k: g["edad"].to_numpy() for k, g in df.groupby(["subtipo", "tiempo", "farmaco", "linea"])}
#         self._g3 = {k: g["activo"].to_numpy() for k, g in df.groupby(["tiempo", "farmaco", "linea"])}
#         self._g2 = {k: g["activo"].to_numpy() for k, g in df.groupby(["tiempo", "farmaco"])}
#
#     def _beta(self, act: np.ndarray, nivel: str) -> DetalleBeta:
#         k, n = int(act.sum()), int(len(act))
#         return DetalleBeta((self.a0 + k) / (self.a0 + self.b0 + n), k, n, nivel)
#
#     def detalle(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> DetalleBeta:
#         lc = linea_cat(linea) if self.estratificar_linea else "todas"
#         if self.usar_covariables:
#             clave = (subtipo(caso["diagnostico"], caso.get("tipo_mnv")), tiempo, farmaco, lc)
#             if clave in self._g4:
#                 act, edades = self._g4[clave], self._g4_edad[clave]
#                 cerca = np.abs(edades - caso["edad"]) <= self.margen_edad
#                 if cerca.sum() >= self.n_min:
#                     return self._beta(act[cerca], "subtipo+edad+tiempo+farmaco+linea")
#                 if len(act) >= self.n_min:
#                     return self._beta(act, "subtipo+tiempo+farmaco+linea")
#         act = self._g3.get((tiempo, farmaco, lc), np.array([]))
#         if len(act) >= self.n_min:
#             return self._beta(act, "tiempo+farmaco+linea")
#         return self._beta(self._g2.get((tiempo, farmaco), np.array([])), "tiempo+farmaco")
#
#     def p(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> float:
#         return self.detalle(caso, farmaco, tiempo, linea).p_mean


# ---------------------------------------------------------------------------
# Línea base de comparación (solo evaluación offline)
# ---------------------------------------------------------------------------

class EstimadorFrecuencias:
    """Frecuencia relativa poblacional, sin datos del paciente:
        P(A=1 | F,T,L) = (n(A=1,F,T,L) + 1) / (n(F,T,L) + 2)
    (con estratificar_linea=False, se ignora la línea).
    Sirve de piso: un método que use datos del paciente debería superarla.
    No la usa la API."""

    def __init__(self, historico: pd.DataFrame, estratificar_linea: bool = True):
        self.nombre = "poblacional" + ("" if estratificar_linea else "_sin_linea")
        self.estratificar_linea = estratificar_linea
        d = preparar(historico)
        claves = ["farmaco", "tiempo"] + (["linea"] if estratificar_linea else [])
        g = d.groupby(claves)["activo"].agg(["sum", "count"])
        self._p = {k: (r["sum"] + 1) / (r["count"] + 2) for k, r in g.iterrows()}

    def p(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> float:
        clave = (farmaco, tiempo, linea_cat(linea)) if self.estratificar_linea else (farmaco, tiempo)
        return float(self._p.get(clave, 0.5))


# ---------------------------------------------------------------------------
# Camino B -- Red bayesiana
# ---------------------------------------------------------------------------

class EstimadorRedBayesiana:
    """Grafo probabilístico (red bayesiana) calculado con FÓRMULAS CLÁSICAS
    de probabilidad sobre tablas de conteo de pandas. Sin librerías de
    aprendizaje automático: cada número se puede reproducir a mano.

    Estructura "factorizada" (la usada por defecto, ADR-03):

        Farmaco --\
        Tiempo  ----> Activo ----> Subtipo  (también depende de Farmaco)
        Linea   --/          \---> Edad
                              \--> Carga_comorbida

    Tablas de probabilidad condicional (todas con suavizado de Laplace:
    se suma 1 a cada celda, igual que un prior Dirichlet uniforme):

        P(A | F,T,L) = (n(A,F,T,L) + 1) / (n(F,T,L) + |A|)
        P(S | A,F)   = (n(S,A,F)   + 1) / (n(A,F)   + |S|)
        P(E | A)     = (n(E,A)     + 1) / (n(A)     + |E|)
        P(C | A)     = (n(C,A)     + 1) / (n(A)     + |C|)

    Regla de Bayes para un paciente:

        P(A=1 | F,T,L,S,E,C) = P(A=1|F,T,L) P(S|1,F) P(E|1) P(C|1)
                               ------------------------------------------
                               suma sobre a en {0,1} del mismo producto

    Si falta un dato del paciente (p.ej. comorbilidades), su factor se
    omite: sumar esa variable sobre todos sus valores da 1 (marginalizar).

    Estructura "completa" (solo para la evaluación offline; rinde peor,
    ver README 12.3): Activo con los 6 factores como padres,
        P(A | F,T,L,S,E,C) = (n(A,F,T,L,S,E,C) + 1) / (n(F,T,L,S,E,C) + |A|)
    y si falta un padre se promedia con su frecuencia (n(x)+1)/(N+|X|).

    Verificado contra la librería pgmpy: mismos resultados a 1e-12
    (tests/test_rtu.py, test_grafo_igual_a_pgmpy).
    """

    NODOS_PACIENTE = ["Farmaco", "Subtipo", "Tiempo", "Linea", "Edad", "Carga_comorbida"]

    def __init__(self, historico: pd.DataFrame, estructura: str = "factorizada",
                 pseudo_conteos: float = 1.0):
        if estructura not in ("factorizada", "completa"):
            raise ValueError("estructura debe ser 'factorizada' o 'completa'")
        self.nombre = f"B_red_{estructura}"
        self.estructura = estructura
        self.alfa = pseudo_conteos
        d = preparar(historico)
        self.datos = pd.DataFrame({
            "Farmaco": d["farmaco"], "Subtipo": d["subtipo"], "Tiempo": d["tiempo"],
            "Linea": d["linea"], "Edad": d["edad_cat"], "Carga_comorbida": d["carga_comorbida"],
            "Activo": d["activo"].astype(int),
        })
        # Valores observados de cada variable (definen |X| en las fórmulas)
        self._estados = {c: sorted(self.datos[c].astype(str).unique()) if c != "Activo" else [0, 1]
                         for c in self.datos.columns}
        self._n_total = len(self.datos)
        if estructura == "factorizada":
            self._t_activo = self._tabla(["Farmaco", "Tiempo", "Linea"])
            self._t_hijos = {
                "Subtipo": (self._tabla(["Activo", "Farmaco", "Subtipo"]), self._tabla(["Activo", "Farmaco"])),
                "Edad": (self._tabla(["Activo", "Edad"]), self._tabla(["Activo"])),
                "Carga_comorbida": (self._tabla(["Activo", "Carga_comorbida"]), self._tabla(["Activo"])),
            }
            self._t_activo_conj = self._tabla(["Activo", "Farmaco", "Tiempo", "Linea"])
        else:
            padres = self.NODOS_PACIENTE
            self._t_conj = self._tabla(["Activo"] + padres)
            self._t_padres = self._tabla(padres)
            self._t_raiz = {p: self._tabla([p]) for p in padres}

    def _tabla(self, columnas: list[str]) -> dict:
        """Conteos n(columnas) como diccionario {tupla de valores: n}."""
        return self.datos.groupby(columnas).size().to_dict()

    @staticmethod
    def _n(tabla: dict, clave) -> int:
        return tabla.get(clave if len(clave) > 1 else clave[0], 0)

    def _evidencia(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> dict:
        ev = {"Farmaco": farmaco, "Tiempo": tiempo, "Linea": linea_cat(linea)}
        if "diagnostico" in caso:
            ev["Subtipo"] = subtipo(caso["diagnostico"], caso.get("tipo_mnv"))
        if "edad" in caso:
            ev["Edad"] = edad_cat(caso["edad"])
        if all(c in caso for c in COMORBILIDADES):
            ev["Carga_comorbida"] = carga_cat(sum(int(caso[c]) for c in COMORBILIDADES))
        # un valor que nunca aparece en el histórico se trata como dato faltante
        return {k: v for k, v in ev.items() if v in self._estados[k]}

    def _p_factorizada(self, ev: dict) -> float:
        a = self.alfa
        f, t, l = ev.get("Farmaco"), ev.get("Tiempo"), ev.get("Linea")
        puntaje = {}
        for act in (0, 1):
            # P(A | F,T,L)
            n_conj = self._n(self._t_activo_conj, (act, f, t, l))
            n_pad = self._n(self._t_activo, (f, t, l))
            prod = (n_conj + a) / (n_pad + a * 2)
            # P(hijo | A, ...) para cada dato conocido del paciente
            if "Subtipo" in ev:
                t_hijo, t_pad = self._t_hijos["Subtipo"]
                prod *= ((self._n(t_hijo, (act, f, ev["Subtipo"])) + a)
                         / (self._n(t_pad, (act, f)) + a * len(self._estados["Subtipo"])))
            for hijo in ("Edad", "Carga_comorbida"):
                if hijo in ev:
                    t_hijo, t_pad = self._t_hijos[hijo]
                    prod *= ((self._n(t_hijo, (act, ev[hijo])) + a)
                             / (self._n(t_pad, (act,)) + a * len(self._estados[hijo])))
            puntaje[act] = prod
        return puntaje[1] / (puntaje[0] + puntaje[1])

    def _p_completa(self, ev: dict) -> float:
        from itertools import product
        a = self.alfa
        faltan = [p for p in self.NODOS_PACIENTE if p not in ev]
        opciones = [self._estados[p] for p in faltan]
        total = 0.0
        for valores in product(*opciones):
            completo = {**ev, **dict(zip(faltan, valores))}
            peso = 1.0
            for p, v in zip(faltan, valores):  # frecuencia de cada padre faltante
                peso *= (self._n(self._t_raiz[p], (v,)) + a) / (self._n_total + a * len(self._estados[p]))
            clave = tuple(completo[p] for p in self.NODOS_PACIENTE)
            p1 = (self._n(self._t_conj, (1,) + clave) + a) / (self._n(self._t_padres, clave) + a * 2)
            total += peso * p1
        return total

    def p(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> float:
        ev = self._evidencia(caso, farmaco, tiempo, linea)
        if self.estructura == "factorizada":
            return self._p_factorizada(ev)
        return self._p_completa(ev)


# ---------------------------------------------------------------------------
# Adaptador al motor de Markov y recomendación
# ---------------------------------------------------------------------------

def p_activo_para(estimador: Estimador, caso: dict, farmaco: str,
                  linea: int = 1) -> Callable[[EstadoCiclo], float]:
    cache: dict[str, float] = {}

    def f(e: EstadoCiclo) -> float:
        t = tiempo_de_estado(e)
        if t not in cache:
            cache[t] = estimador.p(caso, farmaco, t, linea)
        return cache[t]
    return f


@dataclass
class Recomendacion:
    por_farmaco: dict[str, ResultadoMarkov]
    orden: list[str]
    valor_orden: dict[str, float]
    objetivo: str


def recomendar(estimador: Estimador, caso: dict, farmacos: list[str],
               linea: int = 1, objetivo: str = "estable",
               sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> Recomendacion:
    """Para un ojo que arranca (o re-arranca tras switch) en `linea`,
    corre la cadena de Markov de cada fármaco candidato con p_activo
    condicionado al paciente y devuelve el orden sugerido.
    Objetivo por defecto: "estable" (maximizar P(estabilidad), ADR-13).
    Simplificación: todos los candidatos se evalúan con la misma línea
    (el segundo de la secuencia en rigor sería línea 2+); se corrige en
    la fase 4 junto con el supuesto de independencia."""
    res = {f: analizar_farmaco(p_activo_para(estimador, caso, f, linea), sup) for f in farmacos}
    orden = orden_optimo(res, objetivo)
    return Recomendacion(res, orden, valor_secuencia(orden, res), objetivo)


# ---------------------------------------------------------------------------
# Fase 4 -- Recomendación consciente de la línea de tratamiento
# ---------------------------------------------------------------------------

def valor_secuencia_posicional(resultados_en_orden: list[ResultadoMarkov]) -> dict[str, float]:
    """Como markov_rtu.valor_secuencia, pero cada posición trae su propio
    ResultadoMarkov (p.ej. el 1ro estimado como línea 1 y los siguientes
    como línea 2+). Se pasa a la siguiente posición solo por switch."""
    llegar = 1.0
    iny = estable = 0.0
    for r in resultados_en_orden:
        iny += llegar * r.inyecciones_esperadas
        estable += llegar * r.prob_absorcion["estable"]
        llegar *= r.prob_absorcion["switch"]
    return {"inyecciones_esperadas": iny, "prob_estable": estable, "prob_agotar_opciones": llegar}


def recomendar_por_linea(estimador: Estimador, caso: dict, farmacos: list[str],
                         objetivo: str = "estable", linea_inicial: int = 1,
                         sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> Recomendacion:
    """Orden de fármacos donde la posición 1 usa p_activo de `linea_inicial`
    y las posiciones siguientes usan p_activo de línea 2+.

    Por qué: quien llega al 2do fármaco YA FALLÓ con el primero. Si la
    respuesta a distintos fármacos no es independiente (hay pacientes
    "malos respondedores" en general), la p_activo relevante para la
    posición 2 no es la de un paciente nuevo sino la de uno que falló:
    eso es justamente lo que mide la estimación de línea 2+. Así la
    dependencia entre fármacos se absorbe en los parámetros por posición.

    Con parámetros que dependen de la posición, el lema de intercambio
    c/(1-s) ya no aplica: se busca exhaustivamente (k! órdenes; con 3-4
    fármacos son 6-24, trivial)."""
    from itertools import permutations

    lin2 = max(2, linea_inicial + 1)
    res1 = {f: analizar_farmaco(p_activo_para(estimador, caso, f, linea_inicial), sup) for f in farmacos}
    res2 = {f: analizar_farmaco(p_activo_para(estimador, caso, f, lin2), sup) for f in farmacos}

    def valor(orden):
        return valor_secuencia_posicional([res1[orden[0]]] + [res2[f] for f in orden[1:]])

    if objetivo == "inyecciones":
        mejor = min(permutations(farmacos), key=lambda o: valor(o)["inyecciones_esperadas"])
    elif objetivo == "estable":
        mejor = max(permutations(farmacos), key=lambda o: valor(o)["prob_estable"])
    else:
        raise ValueError("objetivo debe ser 'inyecciones' o 'estable'")
    return Recomendacion(res1, list(mejor), valor(mejor), objetivo)
