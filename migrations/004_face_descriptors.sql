-- ============================================================
--  Migración 004 — Reconocimiento facial (face-api.js)
--  Ejecutar en el panel de Supabase del CALENDARIO → SQL Editor
--  https://app.supabase.com/project/lqdpirsfzodmbeyoivww/sql
--
--  Guarda el "descriptor" facial (128 números) calculado en el
--  navegador. No guarda fotos. La comparación se hace en el servidor.
-- ============================================================

CREATE TABLE IF NOT EXISTS face_descriptors (
  id          uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     text        NOT NULL,        -- users.id (como texto)
  descriptor  text        NOT NULL,        -- JSON con 128 floats
  nombre      text,
  created_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_face_user ON face_descriptors (user_id);
