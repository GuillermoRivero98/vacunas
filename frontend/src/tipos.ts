// Tipos que reflejan exactamente las respuestas de la API (ver README 10.4).

export type Diagnostico = "DMRE" | "EMD" | "OVCR" | "ORVR";
export type TipoMNV = "MNV1" | "MNV2" | "MNV3";
export type Objetivo = "estable" | "inyecciones";

export interface Salud {
  status: string;
  rtu_historico_cargado: boolean;
  rtu_modelo_entrenado: boolean;
  rtu_entrenando: boolean;
  rtu_compra_precalculada: boolean;
  rtu_ultimo_error: string | null;
}

export interface Info {
  listo: boolean;
  farmacos: string[];
  historico?: { pacientes: number; ojos: number; visitas: number };
  datos_simulados?: boolean;
}

export interface SolicitudPlan {
  diagnostico: Diagnostico;
  tipo_mnv?: TipoMNV;
  edad: number;
  diabetes?: 0 | 1;
  hipertension?: 0 | 1;
  acv_iam_reciente?: 0 | 1;
  tabaquismo?: 0 | 1;
  farmacos_ya_probados: string[];
  objetivo: Objetivo;
  n_casos_similares: number;
}

export interface MetricasFarmaco {
  farmaco: string;
  prob_estable: number;
  prob_switch: number;
  prob_abandono: number;
  inyecciones_esperadas: number;
  visitas_esperadas: number;
  semanas_esperadas: number;
}

export interface BaseCalculo {
  ojos_similares_considerados: number;
  distancia_maxima: number | null;
  ciclos_con_este_farmaco: number;
  desenlaces: Record<string, number>;
  inyecciones_medias_por_ciclo: number | null;
  actividad_observada_en_similares_q6_8?: number;
  actividad_estimada_por_modelo_q6_8?: number;
}

export interface TramoTrayectoria {
  farmaco: string;
  linea: number;
  desenlace: string;
  inyecciones: number;
  semanas: number;
}

export interface CasoSimilar {
  id: string;
  distancia: number;
  subtipo: string;
  edad: number;
  comorbilidades: Record<string, number>;
  trayectoria: TramoTrayectoria[];
  inyecciones_totales: number;
  semanas_seguimiento: number;
  desenlace_final: string;
}

export interface Plan {
  objetivo: Objetivo;
  linea: number;
  farmacos_ya_probados: string[];
  orden_sugerido: string[];
  valor_orden: { inyecciones_esperadas: number; prob_estable: number; prob_agotar_opciones: number };
  por_farmaco: MetricasFarmaco[];
  base_de_calculo: Record<string, BaseCalculo>;
  casos_similares: CasoSimilar[];
  supuestos: Record<string, number>;
  advertencias: string[];
  version_modelo: { pacientes: number; ojos: number; visitas: number; entrenado_en: string };
}

export interface SolicitudCompra {
  horizonte_semanas: number;
  nivel_servicio: number;
  nuevos_ojos_por_semana: number;
}

export interface CompraFarmaco {
  compra_sugerida: number;
  demanda_esperada: number;
  desvio_estandar: number;
  intervalo_95: [number, number];
  de_ojos_en_tratamiento: number;
  de_pacientes_nuevos: number;
  por_cada_ojo_nuevo_en_el_horizonte: number;
  modelo_sin_calibrar: { demanda_esperada: number; desvio_estandar: number; compra_sugerida: number };
}

export interface UsoFarmaco {
  inyecciones_historicas: number;
  proporcion_del_total: number;
  p_como_primer_farmaco: number;
  peso_al_hacer_switch: number;
}

export interface Compra {
  corte_semana: number;
  horizonte_semanas: number;
  nivel_servicio: number;
  nuevos_ojos_por_semana: number;
  ojos_en_tratamiento: number;
  por_farmaco: Record<string, CompraFarmaco>;
  uso_historico: Record<string, UsoFarmaco>;
  calibracion: {
    factor: number;
    inflacion: number;
    ventanas: number[];
    advertencia: string | null;
    backtests: { corte: number; farmaco: string; predicho: number; real: number; "error_%": number | null }[];
  };
  metodo: string;
  supuestos: string[];
  advertencias: string[];
}
