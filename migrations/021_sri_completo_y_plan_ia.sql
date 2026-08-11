-- =============================================================================
-- 021_sri_completo_y_plan_ia.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Cierra dos cosas que quedaron a medias en la 020:
--
--   1) DEL SRI SE LEÍAN DATOS QUE NO SE GUARDABAN. `agenteRetencion` y
--      `contribuyenteEspecial` se extraían de la respuesta y se perdían: sólo
--      quedaban enterrados dentro de ruc_raw, sin columna ni sitio donde verlos.
--      Para un estudio contable saber si un contribuyente es agente de retención
--      o especial no es un dato accesorio, es lo que decide cómo se le factura.
--
--   2) LA PLANIFICACIÓN DE LA IA SE PERDÍA AL CERRAR. La ruta crítica, los
--      riesgos y el resumen se enseñaban una vez en la ventana y desaparecían.
--      Ahora se guardan con el plan, que es donde sirven.
--
-- Idempotente. Aplicar en el editor SQL de Supabase DESPUÉS de la 020.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
--  1. Datos del SRI que faltaban por guardar
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ruc_agente_retencion       text;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ruc_contribuyente_especial text;
-- Fecha en que el propio SRI actualizó por última vez la ficha del
-- contribuyente (distinta de updated_at, que es cuándo lo tocamos nosotros).
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ruc_updated_at             date;
-- Cuándo consultamos nosotros al SRI: permite saber si el dato está rancio.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS sri_checked_at             timestamptz;

-- ─────────────────────────────────────────────────────────────────────────────
--  2. Resultado de la planificación asistida, guardado con el plan
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE gantt_plans ADD COLUMN IF NOT EXISTS ai_resumen      text;
ALTER TABLE gantt_plans ADD COLUMN IF NOT EXISTS ai_riesgos      jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE gantt_plans ADD COLUMN IF NOT EXISTS ai_ruta_critica jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE gantt_plans ADD COLUMN IF NOT EXISTS ai_generado_en  timestamptz;
