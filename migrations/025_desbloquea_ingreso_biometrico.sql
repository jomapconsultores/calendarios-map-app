-- =============================================================================
-- 025_desbloquea_ingreso_biometrico.sql
-- Desarrollado por Marco Antonio Posligua San Martín
--
-- El ingreso con huella / Face ID y con rostro estaba COMPLETO —rutas, pantalla
-- de alta en el perfil, botones en el login, webauthn.js y faceid.js— y aun así
-- no funcionaba nunca. La causa no estaba en el código sino aquí:
--
--   webauthn_credentials  RLS activo, 0 políticas
--   face_descriptors      RLS activo, 0 políticas
--   ms_account_permissions RLS activo, 0 políticas
--
-- En PostgreSQL, RLS activo SIN NINGUNA POLÍTICA no significa «sin
-- restricciones»: significa «deniega todo». Y como la aplicación entra con la
-- llave anónima —que sí respeta RLS—, no podía ni leer ni escribir esas tablas.
-- El resultado era el peor posible: no daba error, simplemente el alta se
-- perdía y la lista salía siempre vacía. Un botón que no hace nada y no se
-- queja.
--
-- Es exactamente el mismo fallo que corrigió la migración 022 en las tablas de
-- Directorio y Cronograma. Entonces estas tres se dejaron comentadas a la
-- espera de decidir; se decide ahora: se desactiva RLS, igual que en el resto
-- del sistema.
--
-- POR QUÉ SIN RLS Y NO CON POLÍTICAS: en este sistema quien decide qué ve cada
-- persona es Python (user_can, get_active_role_grants), no la base. Añadir
-- políticas aquí y en ningún otro sitio dejaría dos guardianes distintos
-- diciendo cosas distintas, que es peor que uno solo bien hecho. Si algún día
-- se pasa la autorización a la base, se hace en todas las tablas a la vez.
--
-- Idempotente. Aplicar después de la 024.
-- =============================================================================

ALTER TABLE webauthn_credentials   DISABLE ROW LEVEL SECURITY;
ALTER TABLE face_descriptors       DISABLE ROW LEVEL SECURITY;
ALTER TABLE ms_account_permissions DISABLE ROW LEVEL SECURITY;
