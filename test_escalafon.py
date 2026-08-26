# -*- coding: utf-8 -*-
"""Prueba del escalafón sin base de datos: se sustituye la lectura de `users`
y `roles` por datos de mentira y se comprueba quién puede asignarle a quién."""
import sys, os
sys.path.insert(0, os.getcwd())
from flask import Flask, g
import app as appmod

USERS = [
    {'id': 'u-adm',  'full_name': 'Ana Admin',      'email': 'ana@x.com',   'position': 'Gerencia',   'is_active': True,  'active_role_id': 'r-adm',  'role': 'admin'},
    {'id': 'u-soc',  'full_name': 'Sergio Socio',   'email': 'sergio@x.com','position': 'Socio',      'is_active': True,  'active_role_id': 'r-soc',  'role': 'user'},
    {'id': 'u-fun',  'full_name': 'Fátima Funcio',  'email': 'fati@x.com',  'position': 'Analista',   'is_active': True,  'active_role_id': 'r-fun',  'role': 'user'},
    {'id': 'u-fun2', 'full_name': 'Félix Funcio',   'email': 'felix@x.com', 'position': 'Analista',   'is_active': True,  'active_role_id': 'r-fun',  'role': 'user'},
    {'id': 'u-sec',  'full_name': 'Sara Secretaría','email': 'sara@x.com',  'position': 'Secretaría', 'is_active': True,  'active_role_id': 'r-sec',  'role': 'user'},
    {'id': 'u-baja', 'full_name': 'Bruno Baja',     'email': 'bruno@x.com', 'position': '',           'is_active': False, 'active_role_id': 'r-fun',  'role': 'user'},
]
ROLES = [
    {'id': 'r-adm', 'level': 'administrador'},
    {'id': 'r-soc', 'level': 'socio'},
    {'id': 'r-fun', 'level': 'funcionario'},
    {'id': 'r-sec', 'level': 'secretaria'},
]

class FakeSupabase:
    def get(self, tabla, filtros=None, select=None):
        return {'users': USERS, 'roles': ROLES}.get(tabla, [])

flask_app = Flask(__name__)
flask_app.supabase = FakeSupabase()

fallos = []
def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + ' ' + titulo)
    if not ok:
        print('        esperado:', esperado, '| obtenido:', obtenido)
        fallos.append(titulo)

with flask_app.test_request_context():
    nombres = lambda uid, adm=False: sorted(
        p['nombre'] for p in appmod.personas_asignables(flask_app, uid, mando_total=adm))
    g.pop('_personas_sistema', None)

    print('\n-- A quién ve cada uno en el desplegable --')
    check('el admin ve a todos los de alta (el dado de baja no)',
          nombres('u-adm', True),
          ['Ana Admin', 'Fátima Funcio', 'Félix Funcio', 'Sara Secretaría', 'Sergio Socio'])
    check('el socio se ve a sí mismo y a los de abajo, no al admin',
          nombres('u-soc'), ['Fátima Funcio', 'Félix Funcio', 'Sara Secretaría', 'Sergio Socio'])
    check('el funcionario se ve a sí y a secretaría, no a su colega ni al socio',
          nombres('u-fun'), ['Fátima Funcio', 'Sara Secretaría'])
    check('secretaría sólo se ve a sí misma',
          nombres('u-sec'), ['Sara Secretaría'])

    print('\n-- Quién puede poner a quién de responsable --')
    v = lambda uid, nom, mail=None, adm=False: appmod.validar_responsable(
        flask_app, uid, nom, mail, mando_total=adm)
    check('el admin puede poner al socio',        v('u-adm', 'Sergio Socio', None, True) is None, True)
    check('el funcionario puede ponerse a sí mismo', v('u-fun', 'Fátima Funcio') is None, True)
    check('el funcionario puede poner a secretaría', v('u-fun', 'Sara Secretaría') is None, True)
    check('el funcionario NO puede poner al socio',  v('u-fun', 'Sergio Socio') is not None, True)
    check('el funcionario NO puede poner a su colega de rango', v('u-fun', 'Félix Funcio') is not None, True)
    check('secretaría NO puede poner al funcionario', v('u-sec', 'Fátima Funcio') is not None, True)
    check('el socio SÍ puede poner al funcionario',  v('u-soc', 'Fátima Funcio') is None, True)
    check('el socio NO puede poner al admin',        v('u-soc', 'Ana Admin') is not None, True)

    print('\n-- Rodeos que no deben funcionar --')
    check('sin tildes ni mayúsculas se reconoce igual', v('u-fun', 'sergio  SOCIO') is not None, True)
    check('«Fatima» sin tilde sigue siendo ella misma', v('u-fun', 'fatima funcio') is None, True)
    check('por el correo del socio tampoco', v('u-fun', 'Un Tercero', 'SERGIO@x.com') is not None, True)
    check('un tercero de fuera sí se puede poner', v('u-fun', 'Perito Externo', 'perito@otro.com') is None, True)
    check('vacío no rompe', v('u-fun', '', '') is None, True)

print('\n' + ('TODO CORRECTO' if not fallos else f'{len(fallos)} FALLO(S): ' + ', '.join(fallos)))
sys.exit(1 if fallos else 0)
