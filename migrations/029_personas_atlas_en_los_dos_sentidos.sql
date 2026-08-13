-- =============================================================================
-- 029_personas_atlas_en_los_dos_sentidos.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- Las personas de ATLAS —padres de familia, socios y docentes— pasan de
-- importarse una vez a sincronizarse SOLAS y en los DOS sentidos: lo que se
-- corrige allá aparece aquí, y lo que se corrige aquí vuelve allá.
--
-- Hacen falta cuatro columnas en `contacts`, las mismas cuatro que ya hicieron
-- falta para las reuniones en la 024 y por las mismas razones:
--
--   * atlas_persona_id — el identificador de esa persona en la base de ATLAS.
--     Es lo que permite saber que este contacto y aquella persona son EL MISMO,
--     y no volver a crearlo en cada pasada. Va como texto y no como entero
--     porque no se sabe de antemano si ATLAS usa enteros o uuid, y esta columna
--     no puede depender de acertar esa apuesta.
--
--   * atlas_tabla — de qué tabla de ATLAS salió (representantes, docentes...).
--     Sin esto, dos personas con el mismo id en tablas distintas se
--     confundirían entre sí.
--
--   * atlas_hash — huella del contenido en la última sincronización. ATLAS no
--     garantiza una fecha de modificación en estas tablas, así que no hay a
--     quién preguntarle «¿cambió allá?». Comparando contra esta huella sí: si
--     lo de ATLAS ya no coincide, cambió allá; si lo nuestro ya no coincide,
--     cambió aquí; si no coincide ninguno, cambiaron los dos.
--
--   * atlas_synced_at — cuándo se cruzó por última vez.
--
-- LO QUE ESTA SINCRONIZACIÓN NO HACE, Y A PROPÓSITO: borrar. Se crean y se
-- actualizan personas en los dos sentidos, pero nadie borra a nadie. Que una
-- fila desaparezca de un lado no es prueba de que deba desaparecer del otro —
-- puede ser un filtro, un permiso o un error— y un padre de familia borrado en
-- el sistema del colegio por un descuido de este lado no se recupera con un
-- «deshacer».
--
-- Idempotente. Aplicar después de la 028.
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql
-- =============================================================================

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS atlas_persona_id text;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS atlas_tabla      text;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS atlas_hash       text;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS atlas_synced_at  timestamptz;

-- Una persona de ATLAS no puede quedar enlazada a dos contactos distintos. El
-- índice parcial deja fuera los contactos que no vienen de ATLAS, que son la
-- mayoría y que no tienen por qué cargar con esta restricción.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_atlas_persona
  ON contacts (atlas_tabla, atlas_persona_id)
  WHERE atlas_persona_id IS NOT NULL;

-- Buscar «lo que vino de ATLAS» es lo primero que hace cada pasada.
CREATE INDEX IF NOT EXISTS idx_contacts_atlas_tabla
  ON contacts (atlas_tabla)
  WHERE atlas_tabla IS NOT NULL;
