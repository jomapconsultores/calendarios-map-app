-- ============================================================
--  Migración 003 — Credenciales WebAuthn (Face ID / huella / passkeys)
--  Ejecutar en el panel de Supabase del CALENDARIO → SQL Editor
--  https://app.supabase.com/project/lqdpirsfzodmbeyoivww/sql
-- ============================================================

CREATE TABLE IF NOT EXISTS webauthn_credentials (
  id            uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id       text        NOT NULL,             -- users.id (como texto, sin FK por compatibilidad de tipos)
  credential_id text        NOT NULL UNIQUE,      -- ID de la credencial (base64url)
  public_key    text        NOT NULL,             -- clave pública (base64url)
  sign_count    bigint      DEFAULT 0,
  transports    text,                             -- ej. "internal,hybrid"
  nombre        text,                             -- nombre amigable del dispositivo
  created_at    timestamptz DEFAULT now(),
  last_used_at  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials (user_id);
