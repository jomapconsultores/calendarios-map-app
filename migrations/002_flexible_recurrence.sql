-- ============================================================
--  Migración 002 — Recurrencia flexible
--  Ejecutar en el panel de Supabase del CALENDARIO → SQL Editor
--  Proyecto: lqdpirsfzodmbeyoivww  (el del calendario, NO jomap-sistema)
--  https://app.supabase.com/project/lqdpirsfzodmbeyoivww/sql
--
--  Es idempotente e incluye también las columnas de la 001,
--  así que basta con correr esta sola si la 001 nunca se aplicó.
-- ============================================================

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS is_recurring        boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS recurrence_days     text,        -- (legado 001) lista de días semanal
  ADD COLUMN IF NOT EXISTS recurrence_end_date date,        -- fecha fin (modo "hasta")
  ADD COLUMN IF NOT EXISTS parent_event_id     uuid,        -- agrupa la serie
  ADD COLUMN IF NOT EXISTS recurrence_rule     text;        -- JSON: {freq, interval, weekdays, end_mode, end_date, count}

-- Índice para borrar/consultar series por su evento padre
CREATE INDEX IF NOT EXISTS idx_appointments_parent_event_id
  ON appointments (parent_event_id);
