import { useEffect, useRef, useState } from "react";
import { api, ErrorApi } from "./api";
import type { Compra, Info, Plan, SolicitudCompra, SolicitudPlan } from "./tipos";
import { num } from "./formato";
import { FormularioPaciente } from "./componentes/FormularioPaciente";
import { ResultadoPlan } from "./componentes/ResultadoPlan";
import { COMPRA_POR_DEFECTO, PanelCompras } from "./componentes/PanelCompras";
import { Aviso } from "./componentes/Aviso";

type EstadoServidor =
  | { fase: "conectando" | "preparando" }
  | { fase: "listo"; info: Info }
  | { fase: "sin_historico" | "error"; mensaje: string };

type Pestana = "paciente" | "compras";

const esperar = (ms: number) => new Promise((r) => setTimeout(r, ms));
const mensajes = (e: unknown) => (e instanceof ErrorApi ? e.detalles : ["Ocurrió un error inesperado."]);

export default function App() {
  const [servidor, setServidor] = useState<EstadoServidor>({ fase: "conectando" });
  const [pestana, setPestana] = useState<Pestana>("paciente");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [errorPlan, setErrorPlan] = useState<string[]>([]);
  const [calculandoPlan, setCalculandoPlan] = useState(false);
  const [compra, setCompra] = useState<Compra | null>(null);
  const [errorCompra, setErrorCompra] = useState<string[]>([]);
  const [calculandoCompra, setCalculandoCompra] = useState(false);
  const compraPedida = useRef(false);

  // En el plan gratuito de Render el servidor se duerme: /health lo despierta
  // y dispara la preparación del modelo; se consulta hasta que esté listo.
  useEffect(() => {
    let vivo = true;
    (async () => {
      for (let intento = 0; vivo; intento++) {
        try {
          const s = await api.salud();
          if (!vivo) return;
          if (!s.rtu_historico_cargado) {
            setServidor({ fase: "sin_historico", mensaje: "Todavía no se cargó el histórico de tratamientos en el servidor." });
            return;
          }
          if (s.rtu_ultimo_error) {
            setServidor({ fase: "error", mensaje: `El servidor no pudo preparar el modelo: ${s.rtu_ultimo_error}` });
            return;
          }
          if (s.rtu_modelo_entrenado) {
            const info = await api.info();
            if (info.listo) {
              if (vivo) setServidor({ fase: "listo", info });
              return;
            }
          }
          setServidor({ fase: "preparando" });
        } catch (e) {
          // 404: el servidor responde pero le falta un endpoint (versión vieja).
          // Reintentar no lo arregla: se informa en el acto.
          if (e instanceof ErrorApi && e.estado === 404) {
            if (vivo) setServidor({ fase: "error", mensaje: e.detalles.join(" ") });
            return;
          }
          if (intento >= 5) {
            setServidor({ fase: "error", mensaje: mensajes(e).join(" ") });
            return;
          }
        }
        await esperar(4000);
      }
    })();
    return () => { vivo = false; };
  }, []);

  const listo = servidor.fase === "listo";

  const calcularPlan = async (s: SolicitudPlan) => {
    setCalculandoPlan(true);
    setErrorPlan([]);
    try {
      setPlan(await api.plan(s));
    } catch (e) {
      setErrorPlan(mensajes(e));
    } finally {
      setCalculandoPlan(false);
    }
  };

  const calcularCompra = async (s: SolicitudCompra) => {
    setCalculandoCompra(true);
    setErrorCompra([]);
    try {
      setCompra(await api.compra(s));
    } catch (e) {
      setErrorCompra(mensajes(e));
    } finally {
      setCalculandoCompra(false);
    }
  };

  // Al abrir Compras por primera vez con el servidor listo, se muestra la
  // estimación por defecto (está precalculada).
  useEffect(() => {
    if (pestana === "compras" && listo && !compraPedida.current) {
      compraPedida.current = true;
      void calcularCompra(COMPRA_POR_DEFECTO);
    }
  }, [pestana, listo]);

  return (
    <div className="app">
      <header className="cabecera">
        <div>
          <h1>Plan de tratamiento intravítreo</h1>
          <p className="cabecera__bajada">Qué fármaco anti-VEGF probar primero y cuánto comprar, calculado con probabilidades sobre el histórico de la unidad.</p>
        </div>
        <EstadoLinea servidor={servidor} />
      </header>

      <nav className="pestanas" role="tablist" aria-label="Secciones">
        <button role="tab" id="tab-paciente" aria-selected={pestana === "paciente"} aria-controls="panel-paciente"
          onClick={() => setPestana("paciente")}>Paciente</button>
        <button role="tab" id="tab-compras" aria-selected={pestana === "compras"} aria-controls="panel-compras"
          onClick={() => setPestana("compras")}>Compras</button>
      </nav>

      {(servidor.fase === "error" || servidor.fase === "sin_historico") && (
        <Aviso tipo="error" items={[servidor.mensaje]} />
      )}

      <main>
        <div id="panel-paciente" role="tabpanel" aria-labelledby="tab-paciente" hidden={pestana !== "paciente"} className="panel-paciente">
          <FormularioPaciente farmacos={listo ? servidor.info.farmacos : []} ocupado={calculandoPlan}
            habilitado={listo} onCalcular={calcularPlan} />
          <div className="panel-paciente__resultado">
            <Aviso tipo="error" titulo="No se pudo calcular el plan" items={errorPlan} />
            {plan ? <ResultadoPlan plan={plan} /> : !errorPlan.length && (
              <p className="vacio">Completá los datos del paciente y calculá el plan para ver qué fármaco probar primero y en qué casos se apoya la sugerencia.</p>
            )}
          </div>
        </div>

        <div id="panel-compras" role="tabpanel" aria-labelledby="tab-compras" hidden={pestana !== "compras"}>
          <Aviso tipo="error" titulo="No se pudo calcular la compra" items={errorCompra} />
          <PanelCompras compra={compra} ocupado={calculandoCompra} habilitado={listo} onCalcular={calcularCompra} />
        </div>
      </main>

      <footer className="pie">
        <p>Herramienta de apoyo: la decisión final es del médico tratante.</p>
        {listo && servidor.info.datos_simulados && <p>Los resultados se basan en datos simulados, no en pacientes reales.</p>}
      </footer>
    </div>
  );
}

function EstadoLinea({ servidor }: { servidor: EstadoServidor }) {
  let texto: string;
  switch (servidor.fase) {
    case "conectando":
      texto = "Conectando con el servidor. Si estaba inactivo, puede tardar hasta un minuto.";
      break;
    case "preparando":
      texto = "Preparando el modelo con el histórico.";
      break;
    case "listo": {
      const h = servidor.info.historico;
      texto = h ? `Listo. Histórico de ${num(h.pacientes)} pacientes y ${num(h.ojos)} ojos.` : "Listo.";
      break;
    }
    default:
      texto = "Sin conexión con el modelo.";
  }
  return (
    <p className={`estado estado--${servidor.fase}`} role="status">
      <span className="estado__punto" aria-hidden="true" />
      {texto}
    </p>
  );
}
