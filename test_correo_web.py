# -*- coding: utf-8 -*-
"""Prueba de la puerta del correo en el servidor y de su paso al cronograma.

Sin base de verdad: una base de mentira en memoria, con los mismos métodos que
el cliente de la aplicación (`get`, `insert`, `update`, `upsert`), y una
aplicación Flask mínima con sólo este módulo montado.

Lo que se vigila:

  * que sin el secreto del cron no entre nada;
  * que una segunda publicación ACTUALICE y no duplique — la computadora publica
    cada mañana todo lo relacionado, no sólo lo nuevo;
  * que el plazo de un correo llegue al cronograma una sola vez, con
    `source_id` «correo:…» para no chocar con el índice único de `tasks`;
  * que un plazo cambiado mueva la fecha, y una actividad cerrada no se toque;
  * que lo vencido no se lleve al cronograma: llegaría ya en rojo, reclamando
    algo que el correo pedía para antes de leerlo.
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

# Una carpeta de correo vacía: en la computadora que lee el correo, la pantalla
# prefiere su SQLite a lo publicado, y aquí se quiere probar lo publicado.
import tempfile
os.environ['CORREO_DESTINO'] = tempfile.mkdtemp(prefix='correo_web_')

from flask import Flask
from flask_login import LoginManager

# Se carga el archivo solo, sin pasar por `app/__init__.py`: importar el paquete
# levanta la aplicación entera, con Google, WebAuthn y demás, y esta prueba no
# necesita nada de eso. El módulo no importa nada de `app`, a propósito.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'correo_web', os.path.join(os.getcwd(), 'app', 'correo_web.py'))
_modulo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_modulo)
registrar_correo = _modulo.registrar_correo

fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


class BaseDeMentira:
    def __init__(self):
        self.tablas = {}
        self.siguiente = 1

    def _t(self, tabla):
        return self.tablas.setdefault(tabla, [])

    def get(self, tabla, filters=None, select='*'):
        filas = self._t(tabla)
        if filters:
            filas = [f for f in filas if all(f.get(k) == v for k, v in filters.items())]
        return [dict(f) for f in filas]

    def insert(self, tabla, data):
        fila = {**data, 'id': data.get('id') or self.siguiente}
        self.siguiente += 1
        self._t(tabla).append(fila)
        return [dict(fila)]

    def update(self, tabla, id_val, data, id_col='id'):
        for f in self._t(tabla):
            if f.get(id_col) == id_val:
                f.update(data)
        return True

    def delete(self, tabla, id_val, id_col='id'):
        self.tablas[tabla] = [f for f in self._t(tabla) if f.get(id_col) != id_val]
        return True

    def upsert(self, tabla, filas, on_conflict, timeout=None):
        for d in filas:
            hallada = next((f for f in self._t(tabla) if f.get(on_conflict) == d[on_conflict]), None)
            if hallada:
                hallada.update(d)
            else:
                self._t(tabla).append(dict(d))
        return True


app = Flask(__name__)
app.config.update(SECRET_KEY='x', CRON_SECRET='secreto', TESTING=True, LOGIN_DISABLED=True)
LoginManager(app)
app.supabase = BaseDeMentira()
registrar_correo(app, {'is_admin': lambda: True, 'user_can': lambda p: True})
cliente = app.test_client()

print('\nSólo el dueño')
_modulo._es_el_dueno = lambda: False
check('otro usuario, aunque sea administrador, no lo ve',
      cliente.get('/correo/api/mensajes').status_code, 403)
check('ni entra a la pantalla', cliente.get('/correo').status_code, 302)
_modulo._es_el_dueno = lambda: True

PRONTO = (date.today() + timedelta(days=5)).isoformat()
LUEGO = (date.today() + timedelta(days=9)).isoformat()
ANTES = (date.today() - timedelta(days=3)).isoformat()


def mensaje(id_, plazo=None, quipux=None, estado=()):
    vinculos = [{'tipo': e, 'clave': 'a@gmail.com' if e != 'contestado' else '',
                 'detalle': None, 'seguro': True} for e in estado]
    if plazo:
        vinculos.append({'tipo': 'plazo', 'clave': plazo, 'detalle': 'fecha escrita',
                         'seguro': False})
    if quipux:
        vinculos.append({'tipo': 'quipux', 'clave': quipux, 'detalle': 'Matriz',
                         'seguro': True})
    return {'id': id_, 'fecha': '2026-10-05T10:00:00-05:00', 'de': 'Ana <ana@x.ec>',
            'asunto': f'Asunto {id_}', 'resumen': 'Remitir hasta…', 'cuentas': 'a@gmail.com',
            'n_adjuntos': 1, 'enlace': 'https://mail.google.com/x', 'vinculos': vinculos}


def publicar(mensajes, secreto='secreto', completo=False):
    r = cliente.post('/correo/api/publicar',
                     data=json.dumps({'mensajes': mensajes, 'completo': completo}),
                     headers={'Content-Type': 'application/json', 'X-Cron-Secret': secreto})
    return r.status_code, r.get_json()


print('\nLa puerta')
codigo, _ = publicar([mensaje('a1')], secreto='otro')
check('sin el secreto no entra', codigo, 401)
check('y no se guardó nada', app.supabase.get('correo_mensajes'), [])

print('\nPublicar')
lote = [mensaje('a1', plazo=PRONTO), mensaje('a2', quipux='DGPG-2050-2026'),
        mensaje('a3', plazo=ANTES)]
codigo, r = publicar(lote)
check('entra con el secreto', (codigo, r['publicados']), (200, 3))
check('una actividad nueva: sólo el plazo por delante', r['cronograma']['creadas'], 1)
codigo, r = publicar(lote)
check('la segunda vez no duplica mensajes', len(app.supabase.get('correo_mensajes')), 3)
check('ni actividades', (r['cronograma']['creadas'], r['cronograma']['sin_cambios']), (0, 1))
tareas = app.supabase.get('tasks', {'source': 'correo'})
check('la actividad lleva su origen sin chocar con el quipux', tareas[0]['source_id'], 'correo:a1')
check('y su fecha', tareas[0]['due_date'], PRONTO)
check('cuelga del proyecto del correo',
      [p['name'] for p in app.supabase.get('projects')], ['Correo — plazos'])
guardado = app.supabase.get('correo_mensajes')[1]
check('los vínculos se guardan en texto JSON', json.loads(guardado['vinculos'])[0]['clave'],
      'DGPG-2050-2026')

print('\nCambios de plazo')
codigo, r = publicar([mensaje('a1', plazo=LUEGO)])
check('el plazo cambiado mueve la fecha', r['cronograma']['actualizadas'], 1)
check('a la nueva', app.supabase.get('tasks', {'source': 'correo'})[0]['due_date'], LUEGO)
app.supabase.update('tasks', tareas[0]['id'], {'status': 'done'})
codigo, r = publicar([mensaje('a1', plazo=PRONTO)])
check('una actividad cerrada se respeta', r['cronograma']['respetadas'], 1)

print('\nLa pantalla')
datos = cliente.get('/correo/api/mensajes').get_json()
check('cuenta lo publicado (el servidor no tiene correo propio)',
      (datos['origen'], datos['cuenta']['total'], datos['cuenta']['quipux']),
      ('plataforma', 3, 1))
datos = cliente.get('/correo/api/mensajes?tipo=plazo').get_json()
check('«con plazo» sólo enseña lo que está por delante',
      [m['id'] for m in datos['mensajes']], ['a1'])
datos = cliente.get('/correo/api/mensajes?q=dgpg-2050').get_json()
check('se busca por número de quipux', [m['id'] for m in datos['mensajes']], ['a2'])

print('\nLo que está por atender')
base = [mensaje('a1', plazo=PRONTO), mensaje('a2', quipux='DGPG-2050-2026', estado=('marcado',)),
        mensaje('a3', plazo=ANTES), mensaje('e1', estado=('marcado', 'sin_leer')),
        mensaje('e2', estado=('sin_leer', 'contestado')), mensaje('e3', estado=('sin_leer',))]
codigo, r = publicar(base, completo=True)
check('entra con su estado', (codigo, r['publicados'], r['atendidos']), (200, 6, 0))
datos = cliente.get('/correo/api/mensajes?tipo=pendiente').get_json()
check('por atender: marcado o sin leer, y sin contestar',
      sorted(m['id'] for m in datos['mensajes']), ['a2', 'e1', 'e3'])
check('con sus cifras', {k: datos['cuenta'][k] for k in ('pendiente', 'marcado', 'sin_leer',
                                                          'relacionado', 'total')},
      {'pendiente': 3, 'marcado': 2, 'sin_leer': 3, 'relacionado': 3, 'total': 6})
datos = cliente.get('/correo/api/mensajes?tipo=marcado').get_json()
check('los marcados', sorted(m['id'] for m in datos['mensajes']), ['a2', 'e1'])

codigo, r = publicar([mensaje('a1', plazo=PRONTO), mensaje('a2', quipux='DGPG-2050-2026'),
                      mensaje('a3', plazo=ANTES), mensaje('e3', estado=('sin_leer',))],
                     completo=True)
check('lo atendido se retira', r['atendidos'], 2)
ids = sorted(f['id'] for f in app.supabase.get('correo_mensajes'))
check('lo que sólo estaba por atender se borra; lo relacionado se queda', ids,
      ['a1', 'a2', 'a3', 'e3'])
a2 = next(f for f in app.supabase.get('correo_mensajes') if f['id'] == 'a2')
check('sin su estado', [v['tipo'] for v in json.loads(a2['vinculos'])], ['quipux'])

codigo, r = publicar([], completo=True)
check('una lista completa vacía también vale: no queda nada por atender',
      (codigo, r['atendidos'], len(app.supabase.get('correo_mensajes'))), (200, 1, 3))
codigo, _ = publicar([])
check('pero vacía y sin decir que es completa, no', codigo, 400)

print()
if fallos:
    print(f'{len(fallos)} FALLA(N)')
    sys.exit(1)
print('Todo bien.')
