-- Precarga los roles base "Administrador" y "Socio" como plantillas, para que
-- los tres niveles (administrador / socio / funcionario) existan listos para usar.
-- El nivel funcionario ya queda representado por "Funcionaria - Tributos" (017).
--
-- Script de datos (una sola vez), idempotente: si un rol con ese nombre ya
-- existe, no lo duplica ni lo modifica.
--
-- IMPORTANTE: estos roles solo traen MODULOS. Los calendarios, proyectos y
-- actividades se marcan luego en Admin -> Roles segun a quien se asignen.
-- Nota: el "Administrador" del SISTEMA (poder total real) sigue siendo el
-- usuario con users.role = 'admin'; este rol de nivel administrador es una
-- plantilla de negocio para agrupar accesos, no otorga privilegios de admin.
--
-- REQUISITO: correr ANTES 016_role_levels_and_activities.sql (usa roles.level).
--
-- Ejecutar en: https://supabase.com/dashboard/project/lqdpirsfzodmbeyoivww/sql

INSERT INTO roles (name, description, level, modules, created_by)
SELECT 'Administrador',
       'Rol de nivel administrador. Plantilla con todos los modulos; marca sus calendarios/proyectos en Admin -> Roles.',
       'administrador', 'calendar,planning,todo', 'seed_base_roles'
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Administrador');

INSERT INTO roles (name, description, level, modules, created_by)
SELECT 'Socio',
       'Rol de nivel socio. Plantilla con Calendario y Planificacion; marca sus calendarios/proyectos en Admin -> Roles.',
       'socio', 'calendar,planning', 'seed_base_roles'
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Socio');
