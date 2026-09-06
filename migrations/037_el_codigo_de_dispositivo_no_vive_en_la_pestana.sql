-- ============================================================
--  Dónde espera una autorización de Microsoft a medio hacer
-- ============================================================
--
-- El código de dispositivo se guardaba en la sesión del navegador de quien lo
-- pidió. Eso ata el trámite a una pestaña: recargarla, cerrarla o irse a otra
-- página borra el único sitio donde estaba apuntado qué se está autorizando, y
-- entonces nadie vuelve a preguntarle a Microsoft si ya lo aprobaron.
--
-- Lo peor es cómo se ve desde fuera: Microsoft dice «ya puede cerrar esta
-- ventana», la persona da por hecho que quedó conectada, y aquí no hay ni
-- rastro. Pasó cuatro veces seguidas con la misma cuenta, y cada vez parecía un
-- problema distinto.
--
-- Con el código en la base, la recogida deja de depender de la pestaña: vale
-- cualquier petición, desde cualquier sitio, mientras el código siga vivo —los
-- quince minutos que da Microsoft—.
--
-- No guarda ningún permiso: sólo el papelito de «hay un trámite en curso para
-- esta cuenta». En cuanto se completa o falla, la fila se borra. El
-- `device_code` no sirve para entrar en nada por sí solo: es la mitad de una
-- conversación que sólo cierra quien aprueba en Microsoft.

CREATE TABLE IF NOT EXISTS ms_autorizaciones (
  email       text PRIMARY KEY,
  device_code text NOT NULL,
  authority   text,
  pedida_en   timestamptz NOT NULL DEFAULT now(),
  expira_en   timestamptz
);

-- Para barrer lo que caducó sin que nadie lo terminara.
CREATE INDEX IF NOT EXISTS ms_autorizaciones_expira_idx
  ON ms_autorizaciones (expira_en);

-- Igual que el resto del sistema (ver 022): el servidor entra con la clave de
-- servicio y filtra por permisos en la aplicación.
ALTER TABLE ms_autorizaciones DISABLE ROW LEVEL SECURITY;
