# -*- coding: utf-8 -*-
"""Prueba de que la agenda entra, no sólo sale.

Sin red y sin base: se le dan a la sincronización eventos de mentira —los que
devolvería Google— y se mira QUÉ escribe.

Lo que se vigila es lo que rompía antes de existir esto:

  * una reunión apuntada en el móvil no llegaba nunca a la plataforma, que
    seguía diciendo que ese hueco estaba libre;
  * mover una cita desde Google no se enteraba nadie aquí;
  * cancelarla allí la dejaba viva aquí, y alguien se presentaba;

y lo que rompería si esto estuviera mal hecho:

  * que lo que la propia plataforma acaba de escribir en Google vuelva en la
    siguiente pasada disfrazado de novedad y se pise a sí mismo — el sistema
    discutiendo consigo mismo;
  * que la misma reunión entre una vez por pasada hasta llenar el día.
"""
import sys, os
sys.path.insert(0, os.getcwd())

from app import agenda_entrante as ent


fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


class SupabaseFalso:
    """Apunta lo que se le manda escribir, y devuelve lo que se le enchufe."""
    def __init__(self, filas=()):
        self.filas = list(filas)
        self.insertados = []
        self.actualizados = []

    def get(self, tabla, filtros=None, select=None):
        return list(self.filas)

    def get_q(self, tabla, params=None, select=None):
        return list(self.filas)

    def insert(self, tabla, data):
        self.insertados.append(data)
        return [{'id': f'nueva-{len(self.insertados)}'}]

    def update(self, tabla, id_val, data, id_col='id'):
        self.actualizados.append((id_val, data))
        return True


class AppFalsa:
    def __init__(self, filas=(), eventos=(), falla=None):
        self.supabase = SupabaseFalso(filas)
        self._eventos = list(eventos)
        self._falla = falla

    def obtener_creds_google(self, cuenta):
        return None if self._falla == 'sin-permiso' else 'credenciales'


def enchufar_google(app):
    """Sustituye la llamada a Google por la lista de eventos de mentira."""
    class _Eventos:
        def list(self, **kw): return self
        def execute(self): return {'items': app._eventos}

    class _Servicio:
        def events(self): return _Eventos()

    ent.build = lambda *a, **k: _Servicio()


CUENTA = 'jomap@ejemplo.com'
CAL = {CUENTA: 'cal-jomap'}


def evento(id_, titulo='Reunión de directorio', updated='2026-09-04T10:00:00.000Z',
           inicio='2026-09-20T15:00:00-05:00', fin='2026-09-20T16:00:00-05:00',
           estado='confirmed', lugar='Sala grande'):
    return {
        'id': id_, 'status': estado, 'updated': updated,
        'summary': titulo, 'location': lugar,
        'start': {'dateTime': inicio}, 'end': {'dateTime': fin},
        'organizer': {'email': 'otro@ejemplo.com', 'displayName': 'OTRO DESPACHO'},
        'attendees': [{'email': CUENTA, 'self': True},
                      {'email': 'tercero@ejemplo.com'}],
    }


def cita(id_='cita-1', updated='2026-09-04T10:00:00.000Z', **campos):
    base = {
        'id': id_, 'title': 'Reunión de directorio', 'status': 'confirmed',
        'calendar_id': 'cal-jomap', 'lugar': 'Sala grande', 'direccion': '',
        'ciudad': '', 'notes': '', 'meeting_link': '',
        'invitados': 'tercero@ejemplo.com', 'encargado': 'OTRO DESPACHO',
        'start_time': '2026-09-20T15:00:00-05:00',
        'end_time': '2026-09-20T16:00:00-05:00',
        'origen': 'externo', 'visto': True,
        'google_event_id': 'ev-1', 'google_cal_id': 'primary',
        'google_account': CUENTA, 'google_updated': updated, 'external_uid': None,
    }
    base.update(campos)
    return base


# --------------------------------------------------------- lo que aparece fuera
print('\n-- Una reunión apuntada fuera, que aquí no existe --')
app = AppFalsa(filas=[], eventos=[evento('ev-nuevo')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)

check('se recoge', res[CUENTA]['nuevas'], 1)
check('y no se toca nada más', len(app.supabase.actualizados), 0)
nueva = app.supabase.insertados[0]
check('entra confirmada: el compromiso ya lo adquirió otro',
      nueva['status'], 'confirmed')
check('se sabe que no la pidió el despacho', nueva['origen'], 'externo')
check('empieza sin mirar, para que se avise', nueva['visto'], False)
check('con el calendario de la cuenta por la que entró',
      nueva['calendar_id'], 'cal-jomap')
check('y atada a su evento, para no volver a entrar en la siguiente pasada',
      (nueva['google_account'], nueva['google_event_id']), (CUENTA, 'ev-nuevo'))
check('se guarda de qué versión venimos', nueva['google_updated'],
      '2026-09-04T10:00:00.000Z')
check('quien convoca queda como encargado', nueva['encargado'], 'OTRO DESPACHO')
check('uno mismo no figura entre sus propios invitados',
      CUENTA in nueva['invitados'], False)

print('\n-- La misma reunión en la siguiente pasada --')
app = AppFalsa(filas=[cita()], eventos=[evento('ev-1')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('no se duplica', len(app.supabase.insertados), 0)
check('ni se apunta como cambio', res[CUENTA]['actualizadas'], 0)
check('y no se escribe nada en la base', len(app.supabase.actualizados), 0)


# ------------------------------------------------------- lo que cambia de lado
print('\n-- La mueven desde el móvil --')
app = AppFalsa(filas=[cita()], eventos=[evento(
    'ev-1', updated='2026-09-05T09:00:00.000Z',
    inicio='2026-09-21T15:00:00-05:00', fin='2026-09-21T16:00:00-05:00')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)

check('el cambio llega aquí', res[CUENTA]['actualizadas'], 1)
_, cambios = app.supabase.actualizados[0]
check('con la fecha nueva', cambios['start_time'], '2026-09-21T15:00:00-05:00')
check('vuelve a pedir que alguien lo mire', cambios['visto'], False)
check('y se apunta hasta qué versión estamos al día',
      cambios['google_updated'], '2026-09-05T09:00:00.000Z')

print('\n-- Lo que cambiamos NOSOTROS no vuelve como novedad --')
# La plataforma acaba de escribir en Google y guardó la marca que Google le
# devolvió. Ese eco no puede tratarse como un cambio hecho fuera: haría que un
# cambio propio se pisara a sí mismo en la siguiente pasada.
app = AppFalsa(filas=[cita(updated='2026-09-05T09:00:00.000Z',
                           title='TÍTULO PUESTO AQUÍ')],
               eventos=[evento('ev-1', updated='2026-09-05T09:00:00.000Z',
                               titulo='Reunión de directorio')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('no se cuenta como cambio', res[CUENTA]['actualizadas'], 0)
check('y el título de aquí se respeta', len(app.supabase.actualizados), 0)


# ---------------------------------------------------------- lo que desaparece
print('\n-- La cancelan allí --')
app = AppFalsa(filas=[cita()], eventos=[evento(
    'ev-1', updated='2026-09-06T09:00:00.000Z', estado='cancelled')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)

check('aquí se marca cancelada', res[CUENTA]['canceladas'], 1)
_, cambios = app.supabase.actualizados[0]
check('con ese estado y no otro', cambios['status'], 'cancelled')
check('NO se borra: una cita que se esfuma no deja saber que existió',
      len(app.supabase.insertados), 0)
check('y se avisa de que algo pasó', cambios['visto'], False)

print('\n-- La cancelan dos veces --')
app = AppFalsa(filas=[cita(status='cancelled')], eventos=[evento(
    'ev-1', updated='2026-09-06T09:00:00.000Z', estado='cancelled')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('la segunda no vuelve a contar', res[CUENTA]['canceladas'], 0)

print('\n-- Algo cancelado que nunca tuvimos --')
app = AppFalsa(filas=[], eventos=[evento('ev-fantasma', estado='cancelled')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('no se recoge una cita para nacer muerta',
      (res[CUENTA]['nuevas'], len(app.supabase.insertados)), (0, 0))


# ------------------------------------------------------- cuando algo va mal
print('\n-- Una cuenta sin permiso --')
app = AppFalsa(filas=[], eventos=[evento('ev-1')], falla='sin-permiso')
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('se dice, en vez de dar la agenda por vacía',
      (res[CUENTA]['error'], res[CUENTA]['nuevas']), ('sin conectar', 0))

print('\n-- Una cuenta sin calendario al que atar lo que entra --')
app = AppFalsa(filas=[], eventos=[evento('ev-1')])
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], {})
check('tampoco se inventa un dueño',
      (bool(res[CUENTA]['error']), len(app.supabase.insertados)), (True, 0))


# --------------------------------------------------- lo que entra por correo
print('\n-- Una invitación por correo --')
LECTURA = {'uid': 'uid-123', 'cancelado': False, 'secuencia': 0,
           'todo_el_dia': False,
           'cita': {'title': 'Sesión del pleno', 'start_time': '2026-09-15T13:00:00+00:00',
                    'end_time': '2026-09-15T14:00:00+00:00', 'calendar_id': 'cal-ms',
                    'encargado': 'secretaria@ejemplo.gob.ec', 'lugar': 'Sala 3',
                    'notes': '', 'invitados': 'secretaria@ejemplo.gob.ec',
                    'meeting_link': '', 'direccion': '', 'ciudad': ''}}

app = AppFalsa()
res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0}
ent._aplicar_invitacion(app, LECTURA, 'csccue@ejemplo.gob.ec', {}, res)
check('entra a la agenda como una cita más', res['nuevas'], 1)
check('atada a su UID, que es su identificador allí',
      app.supabase.insertados[0]['external_uid'], 'uid-123')
check('y marcada como venida de fuera',
      app.supabase.insertados[0]['origen'], 'externo')

print('\n-- La misma invitación reenviada --')
app = AppFalsa()
por_uid = {'uid-123': {**LECTURA['cita'], 'id': 'cita-x', 'status': 'confirmed'}}
res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0}
ent._aplicar_invitacion(app, LECTURA, 'csccue@ejemplo.gob.ec', por_uid, res)
check('no entra dos veces ni se toca',
      (res['nuevas'], res['actualizadas'], len(app.supabase.actualizados)), (0, 0, 0))

print('\n-- Cambian la hora y reenvían --')
app = AppFalsa()
movida = {**LECTURA, 'secuencia': 1,
          'cita': {**LECTURA['cita'], 'start_time': '2026-09-16T13:00:00+00:00'}}
res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0}
ent._aplicar_invitacion(app, movida, 'csccue@ejemplo.gob.ec', por_uid, res)
check('se actualiza la que había, no se crea otra',
      (res['actualizadas'], res['nuevas']), (1, 0))
check('con la hora nueva',
      app.supabase.actualizados[0][1]['start_time'], '2026-09-16T13:00:00+00:00')

print('\n-- Llega la cancelación --')
app = AppFalsa()
res = {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0}
ent._aplicar_invitacion(app, {**LECTURA, 'cancelado': True},
                        'csccue@ejemplo.gob.ec', por_uid, res)
check('la cita queda cancelada aquí también',
      (res['canceladas'], app.supabase.actualizados[0][1]['status']),
      (1, 'cancelled'))

print('\n' + ('TODO CORRECTO' if not fallos else
              '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
