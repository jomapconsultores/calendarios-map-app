-- ============================================================
--  Los encargados dejan de multiplicarse
--
--  Aplicar con:  python tools/aplicar_migracion.py migrations/042_*.sql
-- ============================================================
--
-- Al guardar una cita, el encargado que se hubiera tecleado entraba SOLO en el
-- catálogo (`insert_ignore`). Nadie lo daba de alta a propósito: bastaba con
-- escribirlo una vez en el formulario. Así, la misma persona acabó siendo tres
-- —MAP, MARCO ANTONIO y MARCO—, y el desplegable ofrecía las tres como si
-- fueran gente distinta.
--
-- Eso no es un problema de estética. El encargado se imprime en la invitación
-- que se manda al cliente, sale en el aviso de incumplimiento y es por lo que
-- se agrupa cualquier recuento: tres nombres para una persona son tres
-- responsables que no cuadran con nadie.
--
-- Esto deja la lista como debe estar. El alta automática se quita en el código
-- (ver app/__init__.py): a partir de ahora un encargado se crea a propósito,
-- con el botón, y escribir cualquier cosa en el formulario ya no da de alta a
-- nadie.

-- ------------------------------------------------------------
--  1. Las citas que ya nombran a Marco de otra forma
-- ------------------------------------------------------------
-- PRIMERO las citas y DESPUÉS el catálogo. Al revés quedarían citas apuntando
-- a un encargado que ya no existe, y al abrirlas para editarlas el formulario
-- se negaría a guardar por un nombre que nadie escribió hoy.
UPDATE appointments
   SET encargado = 'MAP'
 WHERE upper(btrim(encargado)) IN ('MARCO', 'MARCO ANTONIO');

-- ------------------------------------------------------------
--  2. El catálogo: sólo MAP y JOHANNA
-- ------------------------------------------------------------
DELETE FROM encargados
 WHERE upper(btrim(name)) IN ('MARCO', 'MARCO ANTONIO');

INSERT INTO encargados (name) VALUES ('MAP')
ON CONFLICT (name) DO NOTHING;

INSERT INTO encargados (name) VALUES ('JOHANNA')
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('042')
ON CONFLICT (version) DO NOTHING;


-- ------------------------------------------------------------
--  Cómo comprobar que quedó bien
-- ------------------------------------------------------------
--   SELECT name FROM encargados ORDER BY name;
--   -- y que ninguna cita quedó nombrando a quien ya no está:
--   SELECT encargado, count(*) FROM appointments
--    WHERE encargado NOT IN (SELECT name FROM encargados)
--    GROUP BY encargado ORDER BY 2 DESC;
