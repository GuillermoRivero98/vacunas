"""
compras_rtu.py  --  Estimación de compra de fármacos (RF-21)

Estima cuántas dosis de cada fármaco se van a necesitar en las próximas
H semanas y cuánto conviene comprar, TODO CON PROBABILIDAD CLÁSICA:
frecuencias, probabilidad condicional, esperanza, varianza y el Teorema
Central del Límite. Nada de aprendizaje automático ni IA.

1. USO HISTÓRICO ("los fármacos que más se usan")
   - P(primer fármaco = f)  = (n_ojos que empezaron con f + 1) / (n_ojos + k)
   - P(pasar a g | switch)  ∝ (n_switches hacia g + 1), renormalizado entre
     los fármacos que el ojo todavía no probó.
   Refleja la práctica de prescripción observada, no la recomendación.

2. DEMANDA DE UN OJO: ESPERANZA Y VARIANZA EXACTAS
   Para un ojo en el estado s del protocolo, con fármaco f y w semanas
   de horizonte, sea X_g = inyecciones del fármaco g en ese horizonte.
   En cada visita, con probabilidad p = p_activo (del grafo) la
   enfermedad está activa; la regla del protocolo da la acción:
     - estable           -> se termina
     - switch            -> se pasa a g' (según el uso histórico) y arranca
                            su carga en la misma visita
     - inyectar y seguir -> próxima visita en q semanas (si q <= w), antes
                            de la cual el paciente abandona con prob. pa
   Con c = 1 si en la visita se inyecta g, y Y = lo que viene después:
       E[X]   = suma sobre ramas de  P(rama) * (c + E[Y])
       E[X^2] = suma sobre ramas de  P(rama) * (c^2 + 2 c E[Y] + E[Y^2])
       Var[X] = E[X^2] - E[X]^2
   Recursión exacta (programación dinámica con memoria), no simulación.

3. DEMANDA TOTAL
   - Ojos en tratamiento hoy: se suman esperanzas y varianzas
     (ojos independientes entre sí; los dos ojos de un paciente no lo son
     del todo -> la varianza real puede ser algo mayor, ver limitaciones).
   - Pacientes nuevos: llegadas de Poisson con tasa lambda ojos/semana.
     Para una suma de Poisson de aportes X_t (llegada en la semana t):
       E = lambda * suma_t E[X_t]      Var = lambda * suma_t E[X_t^2]
     El histórico simulado no tiene fechas reales, así que lambda es un
     dato de entrada (default 0), no una estimación.

4. CUÁNTO COMPRAR
   La demanda total es una suma de muchos aportes independientes: por el
   Teorema Central del Límite es aproximadamente Normal. Para un nivel de
   servicio alfa (probabilidad de que la compra alcance):
       compra = techo( E + z_alfa * raiz(Var) )
   SUPUESTO: 1 dosis = 1 inyección = 1 vial.

5. CALIBRACIÓN CON EL PROPIO HISTÓRICO (calibrar())
   El modelo usa la p_activo PROMEDIO de cada perfil, pero los ojos de un
   perfil son heterogéneos. Como las inyecciones no crecen linealmente con
   la actividad, promediar sesga la esperanza (desigualdad de Jensen) y
   subestima la varianza (falta la variabilidad entre ojos). Se corrige
   con backtests en ventanas ANTERIORES al corte:
       factor    = suma(real) / suma(predicho)          (estimador de razón)
       inflacion = raiz( media( z^2 ) ),  z = (real - factor*pred) / (factor*desvío)
   y el pronóstico calibrado es  E' = factor*E,  desvío' = inflacion*factor*desvío.
   Validado: calibrando solo con datos hasta la semana 104, la compra al
   95% alcanzó para los 3 fármacos en la ventana 104-156 (README 12.8).

Validación: Monte Carlo de la misma dinámica (tests) y backtest sobre el
histórico (backtest(): se para en la semana T0, predice (T0, T0+H] usando
solo datos hasta T0 y compara con lo que realmente ocurrió).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from estimacion_rtu import (
    COMORBILIDADES,
    EstimadorRedBayesiana,
    carga_cat,
    edad_cat,
    subtipo,
    tiempo_de_estado,
)
from supuestos_protocolo import (
    ACCION_ESTABLE,
    ACCION_SWITCH,
    SUPUESTOS_DEFAULT,
    EstadoCiclo,
    SupuestosProtocolo,
    transicion,
)

LAPLACE = 1.0


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def estado_desde_etiqueta(etiqueta: str, intervalo: int) -> EstadoCiclo:
    """Reconstruye el EstadoCiclo a partir de la etiqueta del histórico
    (C1, M8, M4_a2, M16_s1, ...)."""
    m = re.match(r"^C(\d+)$", etiqueta)
    if m:
        return EstadoCiclo(fase="carga", dosis_carga_previas=int(m.group(1)) - 1)
    m = re.match(r"^M(\d+)(?:_a(\d+))?(?:_s(\d+))?$", etiqueta)
    if not m:
        raise ValueError(f"Etiqueta de estado inválida: {etiqueta}")
    return EstadoCiclo(fase="mantenimiento", intervalo_semanas=int(m.group(1)),
                       activas_consecutivas_en_min=int(m.group(2) or 0),
                       secas_consecutivas_en_max=int(m.group(3) or 0))


def clave_perfil(caso: dict) -> tuple:
    """El grafo solo depende del paciente a través de estas categorías:
    ojos con el mismo perfil tienen exactamente las mismas probabilidades."""
    return (subtipo(caso["diagnostico"], caso.get("tipo_mnv")), edad_cat(caso["edad"]),
            carga_cat(sum(int(caso.get(c, 0)) for c in COMORBILIDADES)))


def caso_de_fila(fila) -> dict:
    return {"diagnostico": str(fila["diagnostico"]), "tipo_mnv": str(fila["tipo_mnv"]),
            "edad": int(fila["edad"]), **{c: int(fila[c]) for c in COMORBILIDADES}}


# ---------------------------------------------------------------------------
# 1. Uso histórico
# ---------------------------------------------------------------------------

@dataclass
class UsoHistorico:
    farmacos: list[str]
    inyecciones: dict[str, int]
    p_primero: dict[str, float]
    peso_switch: dict[str, float]

    def a_dict(self) -> dict:
        total = sum(self.inyecciones.values()) or 1
        orden = sorted(self.farmacos, key=lambda f: -self.inyecciones[f])
        return {f: {"inyecciones_historicas": self.inyecciones[f],
                    "proporcion_del_total": round(self.inyecciones[f] / total, 4),
                    "p_como_primer_farmaco": round(self.p_primero[f], 4),
                    "peso_al_hacer_switch": round(self.peso_switch[f], 4)} for f in orden}


def uso_historico(df: pd.DataFrame) -> UsoHistorico:
    farmacos = sorted(df["farmaco"].astype(str).unique())
    k = len(farmacos)
    iny = df[df["inyectado"] == 1].groupby("farmaco").size()
    primeros = df[df["nro_farmaco_en_secuencia"] == 1].drop_duplicates(["paciente_id", "ojo"])["farmaco"]
    n_ojos = len(primeros)
    cuenta_primero = primeros.value_counts()
    # destino de cada switch = fármaco de las filas de línea >= 2 al arrancar su carga
    destinos = df[(df["nro_farmaco_en_secuencia"] >= 2)].drop_duplicates(
        ["paciente_id", "ojo", "nro_farmaco_en_secuencia"])["farmaco"].value_counts()
    return UsoHistorico(
        farmacos=farmacos,
        inyecciones={f: int(iny.get(f, 0)) for f in farmacos},
        p_primero={f: (cuenta_primero.get(f, 0) + LAPLACE) / (n_ojos + LAPLACE * k) for f in farmacos},
        peso_switch={f: float(destinos.get(f, 0) + LAPLACE) for f in farmacos},
    )


# ---------------------------------------------------------------------------
# 2. Esperanza y segundo momento exactos (programación dinámica)
# ---------------------------------------------------------------------------

class CalculadoraDemanda:
    def __init__(self, estimador, uso: UsoHistorico,
                 sup: SupuestosProtocolo = SUPUESTOS_DEFAULT):
        self.est = estimador
        self.uso = uso
        self.sup = sup
        self.farmacos = uso.farmacos
        self._idx = {f: i for i, f in enumerate(self.farmacos)}
        self._memo: dict = {}
        self._casos: dict[tuple, dict] = {}
        self._p_cache: dict = {}

    def _p(self, perfil: tuple, farmaco: str, e: EstadoCiclo, linea: int) -> float:
        clave = (perfil, farmaco, tiempo_de_estado(e), min(linea, 2))
        if clave not in self._p_cache:
            self._p_cache[clave] = self.est.p(self._casos[perfil], farmaco, clave[2], linea)
        return self._p_cache[clave]

    def momentos(self, caso: dict, e: EstadoCiclo, farmaco: str,
                 probados: frozenset, w: int) -> tuple[np.ndarray, np.ndarray]:
        """(E[X_g], E[X_g^2]) para cada fármaco g, con una visita AHORA en
        el estado e y w semanas de horizonte restante después de ella."""
        perfil = clave_perfil(caso)
        self._casos.setdefault(perfil, caso)
        return self._mom(perfil, e, farmaco, probados, w)

    def _mom(self, perfil, e, farmaco, probados, w):
        clave = (perfil, e, farmaco, probados, w)
        if clave in self._memo:
            return self._memo[clave]
        k = len(self.farmacos)
        E, E2 = np.zeros(k), np.zeros(k)
        p = self._p(perfil, farmaco, e, len(probados))
        pa = self.sup.prob_abandono_por_visita

        def rama(peso, c, Ey=None, E2y=None):
            nonlocal E, E2
            if Ey is None:
                E += peso * c
                E2 += peso * c  # c es 0/1: c^2 = c
            else:
                E += peso * (c + Ey)
                E2 += peso * (c + 2 * c * Ey + E2y)

        for activo, prob in ((True, p), (False, 1.0 - p)):
            if prob == 0.0:
                continue
            dec = transicion(e, activo, self.sup)
            c = np.zeros(k)
            if dec.inyecta:
                c[self._idx[farmaco]] = 1.0
            if dec.accion == ACCION_ESTABLE:
                rama(prob, c)
            elif dec.accion == ACCION_SWITCH:
                restantes = [g for g in self.farmacos if g not in probados]
                if not restantes:
                    rama(prob, c)
                    continue
                pesos = np.array([self.uso.peso_switch[g] for g in restantes])
                pesos = pesos / pesos.sum()
                for g, q in zip(restantes, pesos):
                    Ey, E2y = self._mom(perfil, EstadoCiclo.inicio(), g, probados | {g}, w)
                    rama(prob * q, c, Ey, E2y)
            else:
                intervalo = dec.intervalo_hasta_proxima
                if intervalo > w:
                    rama(prob, c)
                else:
                    rama(prob * pa, c)
                    Ey, E2y = self._mom(perfil, dec.siguiente, farmaco, probados, w - intervalo)
                    rama(prob * (1 - pa), c, Ey, E2y)
        self._memo[clave] = (E, E2)
        return E, E2


# ---------------------------------------------------------------------------
# 3. Cohorte en tratamiento a una fecha de corte
# ---------------------------------------------------------------------------

@dataclass
class OjoEnCurso:
    paciente_id: int
    ojo: str
    caso: dict
    estado: EstadoCiclo       # estado en la PRÓXIMA visita
    farmaco: str
    probados: frozenset
    espera_semanas: int        # semanas desde el corte hasta la próxima visita


def cohorte_en_tratamiento(df: pd.DataFrame, corte: int,
                           sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> list[OjoEnCurso]:
    """Ojos que al momento `corte` siguen en tratamiento, con el estado de
    su próxima visita. Se usa solo información con semana <= corte."""
    h = df[df["semana"] <= corte].sort_values(
        ["paciente_id", "ojo", "visita_nro", "nro_farmaco_en_secuencia"], kind="stable")
    cohorte = []
    for (pid, ojo), g in h.groupby(["paciente_id", "ojo"], sort=True):
        ultima = g.iloc[-1]
        if ultima["accion"] == ACCION_ESTABLE:
            continue
        if ultima["accion"] == ACCION_SWITCH:
            continue  # switch sin fármacos restantes: el ojo terminó
        e = estado_desde_etiqueta(str(ultima["estado"]), int(ultima["intervalo_transcurrido_semanas"]))
        dec = transicion(e, bool(ultima["activo"]), sup)
        if dec.siguiente is None:
            continue
        proxima = int(ultima["semana"]) + int(dec.intervalo_hasta_proxima)
        if proxima <= corte:
            continue  # la próxima visita debía ocurrir antes del corte y no ocurrió: abandonó
        cohorte.append(OjoEnCurso(
            paciente_id=int(pid), ojo=str(ojo), caso=caso_de_fila(ultima),
            estado=dec.siguiente, farmaco=str(ultima["farmaco"]),
            probados=frozenset(g["farmaco"].astype(str).unique()),
            espera_semanas=proxima - corte,
        ))
    return cohorte


# ---------------------------------------------------------------------------
# 4. Estimación completa
# ---------------------------------------------------------------------------

def estimar_demanda(df: pd.DataFrame, horizonte_semanas: int = 52, nivel_servicio: float = 0.95,
                    nuevos_ojos_por_semana: float = 0.0, corte: int | None = None,
                    estimador=None, sup: SupuestosProtocolo = SUPUESTOS_DEFAULT) -> dict:
    """Demanda de cada fármaco en (corte, corte + horizonte] y compra
    sugerida. Con corte=None se usa la última semana del histórico.
    El estimador y el uso histórico salen SOLO de datos hasta el corte."""
    if corte is None:
        corte = int(df["semana"].max())
    base = df[df["semana"] <= corte]
    est = estimador or EstimadorRedBayesiana(base, "factorizada")
    uso = uso_historico(base)
    calc = CalculadoraDemanda(est, uso, sup)
    k = len(uso.farmacos)
    H = int(horizonte_semanas)
    pa = sup.prob_abandono_por_visita

    # --- ojos en tratamiento hoy ---
    cohorte = cohorte_en_tratamiento(df, corte, sup)
    E_c, V_c = np.zeros(k), np.zeros(k)
    for o in cohorte:
        if o.espera_semanas > H:
            continue
        E, E2 = calc.momentos(o.caso, o.estado, o.farmaco, o.probados, H - o.espera_semanas)
        # antes de esa próxima visita el paciente puede abandonar (prob pa)
        E, E2 = (1 - pa) * E, (1 - pa) * E2
        E_c += E
        V_c += E2 - E ** 2

    # --- pacientes nuevos (Poisson) ---
    E_n, V_n = np.zeros(k), np.zeros(k)
    por_ojo_nuevo = np.zeros(k)
    if True:  # el aporte por ojo nuevo se calcula siempre (se informa aunque lambda = 0)
        ojos = base.sort_values("visita_nro").drop_duplicates(["paciente_id", "ojo"])
        perfiles = {}
        for _, f in ojos.iterrows():
            c = caso_de_fila(f)
            perfiles.setdefault(clave_perfil(c), [c, 0])[1] += 1
        total = sum(n for _, n in perfiles.values())
        suma_E, suma_E2 = np.zeros(k), np.zeros(k)
        for t in range(H):  # llegada en la semana t (visita C1 en t)
            for c, n in perfiles.values():
                pi = n / total
                for f in uso.farmacos:
                    E, E2 = calc.momentos(c, EstadoCiclo.inicio(), f, frozenset({f}), H - t - 1)
                    suma_E += pi * uso.p_primero[f] * E
                    suma_E2 += pi * uso.p_primero[f] * E2
                    if t == 0:
                        por_ojo_nuevo += pi * uso.p_primero[f] * E
        E_n = nuevos_ojos_por_semana * suma_E
        V_n = nuevos_ojos_por_semana * suma_E2

    z = float(norm.ppf(nivel_servicio))
    resultado = {}
    for i, f in enumerate(uso.farmacos):
        E = E_c[i] + E_n[i]
        sd = math.sqrt(max(V_c[i] + V_n[i], 0.0))
        resultado[f] = {
            "demanda_esperada": round(E, 1),
            "desvio_estandar": round(sd, 1),
            "de_ojos_en_tratamiento": round(E_c[i], 1),
            "de_pacientes_nuevos": round(E_n[i], 1),
            "compra_sugerida": int(math.ceil(E + z * sd)),
            "intervalo_95": [round(max(E - 1.96 * sd, 0), 1), round(E + 1.96 * sd, 1)],
            "por_cada_ojo_nuevo_en_el_horizonte": round(por_ojo_nuevo[i], 2),
        }
    return {
        "corte_semana": corte,
        "horizonte_semanas": H,
        "nivel_servicio": nivel_servicio,
        "z": round(z, 3),
        "nuevos_ojos_por_semana": nuevos_ojos_por_semana,
        "ojos_en_tratamiento": len(cohorte),
        "por_farmaco": dict(sorted(resultado.items(), key=lambda kv: -kv[1]["demanda_esperada"])),
        "uso_historico": uso.a_dict(),
        "metodo": ("Esperanza y varianza exactas por programación dinámica sobre la cadena "
                   "de Markov del protocolo; p_activo del grafo probabilístico; compra = "
                   "E + z*desvío (aproximación Normal por el Teorema Central del Límite)."),
        "supuestos": ["1 dosis = 1 inyección = 1 vial",
                      "ojos independientes entre sí (los dos ojos de un paciente no lo son del todo)",
                      "la práctica de prescripción futura es igual a la histórica",
                      "pacientes nuevos: llegadas de Poisson con la tasa ingresada"],
    }


# ---------------------------------------------------------------------------
# 5. Calibración y compra
# ---------------------------------------------------------------------------

N_VENTANAS_CALIBRACION = 4
PASO_VENTANAS_SEMANAS = 13


def calibrar(df: pd.DataFrame, corte: int, horizonte_semanas: int,
             n_ventanas: int = N_VENTANAS_CALIBRACION, paso: int = PASO_VENTANAS_SEMANAS) -> dict:
    """Backtests en ventanas que TERMINAN a más tardar en `corte`:
    cortes_k = corte - H - k*paso (k = 0..n-1). Cada backtest usa solo
    datos hasta su propio corte. Si no hay historia suficiente para
    ninguna ventana, devuelve factor = inflación = 1 y lo advierte."""
    H = int(horizonte_semanas)
    cortes = [corte - H - k * paso for k in range(n_ventanas)]
    cortes = [c for c in cortes if c >= paso]
    detalle, pred_tot, real_tot, pares = [], 0.0, 0, []
    for c in cortes:
        p = estimar_demanda(df, H, 0.5, 0.0, corte=c)["por_farmaco"]
        real = df[(df["semana"] > c) & (df["semana"] <= c + H) & (df["inyectado"] == 1)].groupby("farmaco").size()
        for f, r in p.items():
            obs = int(real.get(f, 0))
            pred_tot += r["demanda_esperada"]
            real_tot += obs
            pares.append((r["demanda_esperada"], r["desvio_estandar"], obs))
            detalle.append({"corte": c, "farmaco": f, "predicho": r["demanda_esperada"], "real": obs,
                            "error_%": round(100 * (r["demanda_esperada"] - obs) / obs, 1) if obs else None})
    if not pares or pred_tot == 0:
        return {"factor": 1.0, "inflacion": 1.0, "ventanas": [], "detalle": [],
                "advertencia": "Historia insuficiente para calibrar: se usa el modelo sin corregir."}
    factor = real_tot / pred_tot
    z = [(obs - factor * E) / (factor * sd) for E, sd, obs in pares if sd > 0]
    inflacion = max(1.0, float(np.sqrt(np.mean(np.square(z))))) if z else 1.0
    return {"factor": round(factor, 4), "inflacion": round(inflacion, 3), "ventanas": cortes,
            "detalle": detalle, "advertencia": None}


def estimar_compra(df: pd.DataFrame, horizonte_semanas: int = 52, nivel_servicio: float = 0.95,
                   nuevos_ojos_por_semana: float = 0.0, corte: int | None = None,
                   calibracion: dict | None = None) -> dict:
    """Pronóstico del modelo + calibración con el histórico + compra sugerida."""
    if corte is None:
        corte = int(df["semana"].max())
    r = estimar_demanda(df, horizonte_semanas, nivel_servicio, nuevos_ojos_por_semana, corte=corte)
    cal = calibracion or calibrar(df, corte, horizonte_semanas)
    z = r["z"]
    for f, v in r["por_farmaco"].items():
        E = cal["factor"] * v["demanda_esperada"]
        sd = cal["inflacion"] * cal["factor"] * v["desvio_estandar"]
        v["modelo_sin_calibrar"] = {"demanda_esperada": v.pop("demanda_esperada"),
                                    "desvio_estandar": v.pop("desvio_estandar"),
                                    "compra_sugerida": v.pop("compra_sugerida")}
        v.pop("intervalo_95")
        v["demanda_esperada"] = round(E, 1)
        v["desvio_estandar"] = round(sd, 1)
        v["intervalo_95"] = [round(max(E - 1.96 * sd, 0), 1), round(E + 1.96 * sd, 1)]
        v["compra_sugerida"] = int(math.ceil(E + z * sd))
    r["por_farmaco"] = {f: {k: v[k] for k in ("compra_sugerida", "demanda_esperada", "desvio_estandar",
                                               "intervalo_95", "de_ojos_en_tratamiento", "de_pacientes_nuevos",
                                               "por_cada_ojo_nuevo_en_el_horizonte", "modelo_sin_calibrar")}
                        for f, v in r["por_farmaco"].items()}
    r["calibracion"] = {k: cal[k] for k in ("factor", "inflacion", "ventanas", "advertencia")}
    r["calibracion"]["backtests"] = cal["detalle"]
    r["metodo"] += " Calibrado con backtests en ventanas anteriores al corte (factor de sesgo e inflación del desvío)."
    r["supuestos"].append("el sesgo medido en ventanas pasadas se mantiene en el horizonte pronosticado")
    return r


# ---------------------------------------------------------------------------
# 6. Backtest sobre el histórico
# ---------------------------------------------------------------------------

def backtest(df: pd.DataFrame, corte: int = 104, horizonte_semanas: int = 52) -> pd.DataFrame:
    """Se para en `corte`, predice (corte, corte+H] con datos <= corte, y
    compara con las inyecciones que realmente ocurrieron en esa ventana
    (sin pacientes nuevos: en el histórico simulado todos empiezan en 0)."""
    pred = estimar_demanda(df, horizonte_semanas, 0.95, 0.0, corte=corte)
    ventana = df[(df["semana"] > corte) & (df["semana"] <= corte + horizonte_semanas) & (df["inyectado"] == 1)]
    real = ventana.groupby("farmaco").size()
    filas = []
    for f, r in pred["por_farmaco"].items():
        E, sd = r["demanda_esperada"], r["desvio_estandar"]
        obs = int(real.get(f, 0))
        filas.append({"farmaco": f, "predicho": E, "desvio": sd, "real": obs,
                      "error_%": round(100 * (E - obs) / obs, 1) if obs else None,
                      "z_del_real": round((obs - E) / sd, 2) if sd else None,
                      "compra_95": r["compra_sugerida"], "alcanzaba": obs <= r["compra_sugerida"]})
    return pd.DataFrame(filas)


if __name__ == "__main__":
    import sys
    import time
    path = sys.argv[1] if len(sys.argv) > 1 else "historico_rtu_SIMULADO.xlsx"
    df = pd.read_csv(path) if path.endswith(".csv") else pd.read_excel(path)
    pd.set_option("display.width", 160)

    print("=" * 78)
    print("Backtest: parado en la semana 104, predice las 52 semanas siguientes")
    print("=" * 78)
    t = time.time()
    print(backtest(df, 104, 52).to_string(index=False))
    print(f"({time.time() - t:.1f} s)")

    print()
    print("=" * 78)
    print("Validación de la calibración: calibra con ventanas <= semana 104, evalúa 104-156")
    print("=" * 78)
    t = time.time()
    v = estimar_compra(df, 52, 0.95, 0.0, corte=104)
    real = df[(df["semana"] > 104) & (df["semana"] <= 156) & (df["inyectado"] == 1)].groupby("farmaco").size()
    print(f"factor={v['calibracion']['factor']}  inflación={v['calibracion']['inflacion']}  ventanas={v['calibracion']['ventanas']}  ({time.time() - t:.1f} s)")
    for f, r in v["por_farmaco"].items():
        ok = "alcanza" if real.get(f, 0) <= r["compra_sugerida"] else "NO alcanza"
        print(f"  {f}: compra 95% {r['compra_sugerida']} | real {real.get(f, 0)} -> {ok}")

    print()
    print("=" * 78)
    print("Estimación de compra a 52 semanas desde el final del histórico (95%)")
    print("=" * 78)
    t = time.time()
    r = estimar_compra(df, 52, 0.95, nuevos_ojos_por_semana=0.0)
    print(f"Ojos en tratamiento al corte (semana {r['corte_semana']}): {r['ojos_en_tratamiento']}  "
          f"| factor={r['calibracion']['factor']} inflación={r['calibracion']['inflacion']}  ({time.time() - t:.1f} s)")
    print(pd.DataFrame({f: {k: x[k] for k in ("compra_sugerida", "demanda_esperada", "desvio_estandar", "intervalo_95",
                                              "por_cada_ojo_nuevo_en_el_horizonte")}
                        for f, x in r["por_farmaco"].items()}).T.to_string())
    print("\nUso histórico:")
    print(pd.DataFrame(r["uso_historico"]).T.to_string())
