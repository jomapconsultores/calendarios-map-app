-- Necesario para poder cerrar la condición de carrera en /register (dos registros
-- simultáneos con el mismo email podrían pasar ambos el chequeo previo). Una vez
-- aplicada esta constraint, el código puede usar insert_on_conflict('users', ..., 'email')
-- de forma segura, igual que ya se hace con otras tablas (ver app/__init__.py).
--
-- Si ya existen emails duplicados en la tabla, este ALTER fallará: limpia los
-- duplicados antes de reintentar.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
    END IF;
END $$;
