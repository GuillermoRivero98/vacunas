"""
Implementación de REFERENCIA del grafo con la librería pgmpy.

No la usa el sistema (el sistema calcula el grafo con fórmulas clásicas en
estimacion_rtu.EstimadorRedBayesiana). Existe solo para que un test
compruebe que las fórmulas dan exactamente lo mismo que pgmpy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from estimacion_rtu import COMORBILIDADES, carga_cat, edad_cat, linea_cat, preparar, subtipo


class EstimadorRedBayesianaPgmpy:
    """Dos estructuras de DAG, seleccionables con `estructura`:

    "completa": Activo con 6 padres directos
        (Farmaco, Subtipo, Tiempo, Linea, Edad, Carga_comorbida).
        Captura cualquier interacción, pero la tabla de Activo tiene
        miles de combinaciones de padres: con un histórico de este
        tamaño muchas celdas quedan vacías o casi, y el prior las
        empuja a 0.5 (maldición de la dimensionalidad).

    "factorizada": Activo <- Farmaco, Tiempo, Linea
                   Subtipo <- Activo, Farmaco   (interacción dx x fármaco)
                   Edad <- Activo
                   Carga_comorbida <- Activo
        Las covariables del paciente se modelan como "síntomas" de la
        actividad. Equivale a efectos aditivos en log-odds (parecido a
        una regresión logística), con ~40 celdas en la tabla de Activo
        en vez de ~2300. Supone que Edad y Carga_comorbida son
        condicionalmente independientes dado Activo.
    """
    ESTRUCTURAS = {
        "completa": [(p, "Activo") for p in
                     ["Farmaco", "Subtipo", "Tiempo", "Linea", "Edad", "Carga_comorbida"]],
        "factorizada": [("Farmaco", "Activo"), ("Tiempo", "Activo"), ("Linea", "Activo"),
                        ("Activo", "Subtipo"), ("Farmaco", "Subtipo"),
                        ("Activo", "Edad"), ("Activo", "Carga_comorbida")],
    }
    NODOS_PACIENTE = ["Farmaco", "Subtipo", "Tiempo", "Linea", "Edad", "Carga_comorbida"]

    def __init__(self, historico: pd.DataFrame, estructura: str = "factorizada",
                 pseudo_conteos: float = 1.0):
        from pgmpy.estimators import BayesianEstimator
        from pgmpy.inference import VariableElimination
        from pgmpy.models import DiscreteBayesianNetwork

        self.nombre = f"B_red_{estructura}"
        d = preparar(historico)
        datos = pd.DataFrame({
            "Farmaco": d["farmaco"], "Subtipo": d["subtipo"], "Tiempo": d["tiempo"],
            "Linea": d["linea"], "Edad": d["edad_cat"], "Carga_comorbida": d["carga_comorbida"],
            "Activo": d["activo"].astype(str),
        })
        modelo = DiscreteBayesianNetwork(self.ESTRUCTURAS[estructura])
        cpds = BayesianEstimator(modelo, datos).get_parameters(prior_type="dirichlet",
                                                               pseudo_counts=pseudo_conteos)
        modelo.add_cpds(*cpds)
        modelo.check_model()
        self.modelo = modelo
        self._ve = VariableElimination(modelo)
        self._estados = {n: modelo.get_cpds(n).state_names[n] for n in modelo.nodes()}
        # CPDs que involucran a Activo (su propia tabla + la de sus hijos)
        self._cpds_activo = [c for c in modelo.get_cpds() if "Activo" in c.variables]

    def _evidencia(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> dict:
        ev = {"Farmaco": farmaco, "Tiempo": tiempo, "Linea": linea_cat(linea)}
        if "diagnostico" in caso:
            ev["Subtipo"] = subtipo(caso["diagnostico"], caso.get("tipo_mnv"))
        if "edad" in caso:
            ev["Edad"] = edad_cat(caso["edad"])
        if all(c in caso for c in COMORBILIDADES):
            ev["Carga_comorbida"] = carga_cat(sum(int(caso[c]) for c in COMORBILIDADES))
        # estados que la red nunca vio se descartan (se marginalizan)
        return {k: v for k, v in ev.items() if v in self._estados[k]}

    def _p_exacta_rapida(self, ev: dict) -> float:
        """Con toda la evidencia presente, P(Activo | ev) es proporcional
        al producto de las entradas de las CPDs que contienen a Activo
        (el resto de la red se cancela al normalizar). Exacto y mucho
        más rápido que VariableElimination."""
        puntaje = {}
        for a in self._estados["Activo"]:
            prod = 1.0
            for cpd in self._cpds_activo:
                idx = tuple(cpd.state_names[v].index(a if v == "Activo" else ev[v])
                            for v in cpd.variables)
                prod *= float(cpd.values[idx])
            puntaje[a] = prod
        return puntaje["1"] / sum(puntaje.values())

    def p(self, caso: dict, farmaco: str, tiempo: str, linea: int) -> float:
        ev = self._evidencia(caso, farmaco, tiempo, linea)
        if all(n in ev for n in self.NODOS_PACIENTE):
            return self._p_exacta_rapida(ev)
        q = self._ve.query(["Activo"], evidence=ev, show_progress=False)
        return float(q.values[q.state_names["Activo"].index("1")])


