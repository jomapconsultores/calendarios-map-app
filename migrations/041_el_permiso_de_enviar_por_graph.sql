-- ============================================================
--  Qué se está autorizando, no sólo para quién
--
--  Aplicar con:  python tools/aplicar_migracion.py migrations/041_*.sql
-- ============================================================
--
-- Hasta ahora una cuenta de Microsoft se autorizaba una vez y se acabó: el
-- permiso era siempre el mismo —SMTP para mandar, IMAP para leer— y no hacía
-- falta apuntar cuál, porque no había más que uno.
--
-- Ya no. El dominio csccue.gob.ec tiene apagado el SMTP autenticado
-- (SmtpClientAuthentication), y ese interruptor cierra el puerto 587 para todo
-- el tenant: da igual que el token sea correcto, Exchange contesta 5.7.139 y
-- no entrega. Las citas se guardaban y los invitados no se enteraban.
--
-- La salida es mandar por la API de Microsoft Graph, que no pasa por ese
-- puerto. Pero Graph es OTRO recurso, y Microsoft emite los permisos por
-- recurso: el que vale para IMAP no vale para Graph, y no se pueden pedir los
-- dos en el mismo trámite. Conectar una cuenta pasa a ser dos rondas seguidas
-- de código de dispositivo, una por recurso.
--
-- De ahí esta columna. El trámite a medio hacer vive en la base desde la 037,
-- precisamente para que recargar la pestaña no lo borre; si al recogerlo no se
-- supiera de qué recurso es el código, el token de Graph se guardaría en el
-- sitio del de Outlook y la lectura de la bandeja se quedaría una hora
-- intentando entrar con un permiso que no le sirve.
--
-- 'outlook' por defecto, que es lo que era todo lo anterior.

ALTER TABLE ms_autorizaciones
  ADD COLUMN IF NOT EXISTS recurso text NOT NULL DEFAULT 'outlook';

INSERT INTO schema_migrations (version) VALUES ('041')
ON CONFLICT (version) DO NOTHING;


-- ------------------------------------------------------------
--  Cómo comprobar que está puesta
-- ------------------------------------------------------------
--   SELECT EXISTS(SELECT 1 FROM information_schema.columns
--                  WHERE table_name='ms_autorizaciones'
--                    AND column_name='recurso');
