-- Ejecutar este SQL en el panel de Supabase → SQL Editor
-- https://app.supabase.com/project/lqdpirsfzodmbeyoivww/sql

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS is_recurring boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS recurrence_days text,
  ADD COLUMN IF NOT EXISTS recurrence_end_date date,
  ADD COLUMN IF NOT EXISTS parent_event_id uuid;
