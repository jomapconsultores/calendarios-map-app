# -*- coding: utf-8 -*-
"""Prueba de que el calendario del correo dice lo mismo que la ficha.

Sin tocar Google ni la base: se sustituye el servicio de Google por uno de
mentira que apunta lo que se le pide, y se comprueba QUÉ se le pidió. Lo que
se vigila es lo que rompía antes:

  * mover una cita de fecha reescribe EL MISMO evento — no crea otro y deja
    el viejo en la fecha vieja;
  * cambiar de calendario sí borra y vuelve a crear, porque un evento no
    salta de un calendario a otro;
  * todo lo que cambia o desaparece se avisa a los invitados;
  * si Google falla, se dice; no se guarda en silencio.
"""
import sys, os
sys.path.insert(0, os.getcwd())
import app as appmod


# ---------------------------------------------------------------- utilidades
class Registro:
    """Apunta cada llamada que se le hace al Google de mentira."""
    def __init__(self):
        self.llamadas = []

    def __call__(self, nombre, **kw):
        self.llamadas.append((nombre, kw))

    def solo(self, nombre):
        return [kw for n, kw in self.llamadas if n == nombre]


class EventosFalsos:
    def __init__(self, registro, falla_en=()):
        self.r = registro
        self.falla_en = falla_en

    def _quizas_falla(self, nombre):
        if nombre in self.falla_en:
            raise RuntimeError(self.falla_en[nombre] if isinstance(self.falla_en, dict)
                               else 'error simulado')

    def delete(self, **kw):
        self.r('delete', **kw); self._quizas_falla('delete')
        return self

    def insert(self, **kw):
        self.r('insert', **kw); self._quizas_falla('insert')
        return self

    def update(self, **kw):
        self.r('update', **kw); self._quizas_falla('update')
        return self

    def execute(self):
        return {'id': 'evento-nuevo-123'}


class ServicioFalso:
    def __init__(self, registro, falla_en=()):
        self._ev = EventosFalsos(registro, falla_en)

    def events(self):
        return self._ev


class SupabaseFalso:
    def get(self, tabla, filtros=None, select=None):
        return []

    def update(self, tabla, id_val, data, id_col='id'):
        return True


class AppFalsa:
    supabase = SupabaseFalso()


# Dos calendarios de DOS CUENTAS distintas, más uno de Microsoft: es el caso
# que antes no existía —todo salía de la misma cuenta— y el que hay que vigilar.
CALENDARIOS = [
    {'calendar_id': 'cal-a', 'name': 'JOMAP', 'email': 'jomap@ejemplo.com',
     'color': '#4f46e5', 'google_cal_id': 'gcal-a',
     'cuenta_email': 'jomap@ejemplo.com', 'proveedor': 'google'},
    {'calendar_id': 'cal-b', 'name': 'ATLAS', 'email': 'atlas@ejemplo.com',
     'color': '#16a34a', 'google_cal_id': 'gcal-b',
     'cuenta_email': 'atlas@ejemplo.com', 'proveedor': 'google'},
    {'calendar_id': 'cal-ms', 'name': 'CSCCUE', 'email': 'csccue@ejemplo.gob.ec',
     'color': '#b91c1c', 'google_cal_id': None,
     'cuenta_email': 'csccue@ejemplo.gob.ec', 'proveedor': 'microsoft'},
]

CITA = {
    'id': 'cita-1', 'calendar_id': 'cal-a',
    'google_event_id': 'ev-viejo', 'google_cal_id': 'gcal-a',
    'google_account': 'jomap@ejemplo.com',
    'status': 'confirmed', 'title': 'REUNIÓN', 'encargado': 'MARCO',
    'tema': 'Revisión anual', 'client_name': 'CLIENTE', 'client_email': 'c@ejemplo.com',
    'start_time': '2026-09-01T15:00:00+00:00', 'end_time': '2026-09-01T16:00:00+00:00',
    'invitados': 'invitado@ejemplo.com', 'lugar': 'OFICINA', 'direccion': 'Calle 1',
    'ciudad': 'CUENCA', 'mapa': '', 'notes': '', 'meeting_link': '',
    'ics_sequence': 0,
}

# Con qué cuenta se pidió permiso en cada llamada. Es lo que distingue «se
# agendó» de «se agendó DONDE TOCABA».
cuentas_pedidas = []
invitaciones_enviadas = []


def preparar(falla_en=(), sin_conectar=()):
    """Enchufa el Google de mentira y devuelve su registro de llamadas."""
    reg = Registro()
    del cuentas_pedidas[:]
    del invitaciones_enviadas[:]

    def _creds(app, email=None):
        cuentas_pedidas.append(email)
        return None if email in sin_conectar else 'credenciales-de-mentira'

    def _invitar(app, apt, cuenta, email_map, metodo='REQUEST', secuencia=0):
        invitaciones_enviadas.append({'cuenta': cuenta, 'metodo': metodo,
                                      'secuencia': secuencia,
                                      'destinos': appmod._invitaciones.destinatarios_de(
                                          apt, email_map, cuenta)})
        return len(invitaciones_enviadas[-1]['destinos']), None

    appmod.get_google_creds = _creds
    appmod.build = lambda *a, **k: ServicioFalso(reg, falla_en)
    appmod._get_calendar_config = lambda app: CALENDARIOS
    appmod._invitaciones.enviar_invitacion = _invitar
    appmod._invitaciones.enviar_cancelacion = (
        lambda app, apt, cuenta, email_map, secuencia=1:
        _invitar(app, apt, cuenta, email_map, metodo='CANCEL', secuencia=secuencia))
    return reg


fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


# ---------------------------------------------------------------- las pruebas
print('\n-- Mover la cita de fecha --')
reg = preparar()
parches, aviso = appmod._reflejar_en_google(
    AppFalsa(), CITA, {'start_time': '2026-09-04T15:00:00+00:00',
                       'end_time':   '2026-09-04T16:00:00+00:00'})
check('reescribe el evento que ya existía', len(reg.solo('update')), 1)
check('NO crea un evento nuevo', len(reg.solo('insert')), 0)
check('NO deja un evento suelto sin borrar', len(reg.solo('delete')), 0)
check('es el mismo evento de siempre', reg.solo('update')[0]['eventId'], 'ev-viejo')
check('con la fecha nueva', reg.solo('update')[0]['body']['start']['dateTime'],
      '2026-09-04T15:00:00+00:00')
check('avisando a los invitados', reg.solo('update')[0]['sendUpdates'], 'all')
check('no hay nada que advertir', aviso, None)
check('no cambian los identificadores', parches, {})

check('se pide el permiso de SU cuenta, no el de otra',
      set(cuentas_pedidas), {'jomap@ejemplo.com'})

print('\n-- Cambiar la cita de calendario (y de cuenta) --')
reg = preparar()
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'calendar_id': 'cal-b'})
check('borra el del calendario anterior', len(reg.solo('delete')), 1)
check('del calendario del que salía', reg.solo('delete')[0]['calendarId'], 'gcal-a')
check('y crea en el nuevo', reg.solo('insert')[0]['calendarId'], 'gcal-b')
check('los dos avisando', [reg.solo('delete')[0]['sendUpdates'],
                           reg.solo('insert')[0]['sendUpdates']], ['all', 'all'])
check('se guarda el identificador nuevo y la cuenta donde quedó', parches,
      {'google_event_id': 'evento-nuevo-123', 'google_cal_id': 'gcal-b',
       'google_account': 'atlas@ejemplo.com'})
check('se borra con la cuenta vieja y se crea con la nueva',
      cuentas_pedidas, ['atlas@ejemplo.com', 'jomap@ejemplo.com'])

print('\n-- La cuenta que convoca no se invita a sí misma --')
reg = preparar()
appmod._reflejar_en_google(AppFalsa(), CITA, {'start_time': '2026-09-04T15:00:00+00:00'})
correos = [a['email'] for a in reg.solo('update')[0]['body']['attendees']]
check('el organizador no figura como invitado', 'jomap@ejemplo.com' in correos, False)
check('los invitados de la ficha sí', 'invitado@ejemplo.com' in correos, True)

print('\n-- Una cita cuyo calendario NO es de Google --')
CITA_MS = dict(CITA, calendar_id='cal-ms', google_event_id=None,
               google_cal_id=None, google_account=None)
reg = preparar()
parches, aviso = appmod._reflejar_en_google(
    AppFalsa(), CITA_MS, {'start_time': '2026-09-04T15:00:00+00:00'})
check('no se toca la API de Google', len(reg.llamadas), 0)
check('sale como invitación desde su propia cuenta',
      [i['cuenta'] for i in invitaciones_enviadas], ['csccue@ejemplo.gob.ec'])
check('con el número de versión subido, para que sustituya a la anterior',
      invitaciones_enviadas[0]['secuencia'], 1)
check('y la cuenta que convoca no se invita a sí misma',
      'csccue@ejemplo.gob.ec' in invitaciones_enviadas[0]['destinos'], False)

reg = preparar()
check('cancelarla la retira por correo, no por Google',
      (appmod.retirar_de_la_agenda(AppFalsa(), CITA_MS),
       invitaciones_enviadas[0]['metodo'], len(reg.llamadas)),
      (True, 'CANCEL', 0))

print('\n-- Una cuenta caída no arrastra a las demás --')
reg = preparar(sin_conectar={'atlas@ejemplo.com'})
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'calendar_id': 'cal-b'})
check('se dice qué cuenta falta, por su nombre',
      bool(aviso and 'atlas@ejemplo.com' in aviso), True)
check('y no se borra el evento que sigue siendo el bueno', len(reg.solo('delete')), 0)

print('\n-- Cuando Google falla --')
reg = preparar(falla_en={'update': 'boom'})
parches, aviso = appmod._reflejar_en_google(
    AppFalsa(), CITA, {'start_time': '2026-09-04T15:00:00+00:00'})
check('se avisa de que el correo se quedó atrás', bool(aviso and 'no se pudo actualizar' in aviso), True)
check('y no se inventan identificadores', parches, {})

reg = preparar(falla_en={'insert': 'boom'})
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'calendar_id': 'cal-b'})
check('mudanza a medias: el identificador se limpia para poder repararlo',
      parches, {'google_event_id': None, 'google_cal_id': None,
                'google_account': None})
check('y se dice que hay que reparar', bool(aviso and 'Reparar eventos' in aviso), True)

print('\n-- Sin Google conectado --')
preparar(sin_conectar={'jomap@ejemplo.com'})
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'start_time': 'x'})
check('se avisa de que el evento no se movió', bool(aviso and 'NO se actualizó' in aviso), True)

print('\n-- Cancelar y borrar --')
reg = preparar()
check('borra avisando a los invitados',
      (appmod._borrar_evento_google(AppFalsa(), CITA),
       reg.solo('delete')[0]['sendUpdates']), (True, 'all'))

reg = preparar(falla_en={'delete': 'HttpError 404 Not Found'})
check('un evento que ya no está en Google cuenta como quitado',
      appmod._borrar_evento_google(AppFalsa(), CITA), True)

reg = preparar(falla_en={'delete': 'HttpError 500 backend error'})
check('un fallo de verdad se reconoce como fallo',
      appmod._borrar_evento_google(AppFalsa(), CITA), False)

reg = preparar()
check('una cita que nunca llegó a Google no da guerra',
      (appmod._borrar_evento_google(AppFalsa(), {'google_event_id': None}), len(reg.llamadas)),
      (True, 0))

reg = preparar()
check('ni se la busca en Google al editarla',
      appmod._reflejar_en_google(AppFalsa(), {'google_event_id': None}, {'title': 'X'}),
      ({}, None))

print('')
print('-- Un evento que ya es de otra cita no se adopta --')
# Antes de crear el evento se busca por título y rango de horas, por si ya
# estaba. Pero dos reuniones llamadas «REUNIÓN» que se solapan son dos
# reuniones: atar la segunda al evento de la primera hacía que editar una
# pisara a la otra. Desde la 038 el índice lo impide, y la cita se quedaba sin
# subir dando un choque en el registro cada cuarto de hora.


class SupabaseConDuenos:
    def __init__(self, duenos):
        self.duenos = dict(duenos)   # evento -> cita que ya lo tiene

    def get_in(self, tabla, columna, valores, select=None):
        return [{'id': cita, 'google_event_id': ev}
                for ev, cita in self.duenos.items() if ev in valores]


class AppConDuenos:
    def __init__(self, duenos):
        self.supabase = SupabaseConDuenos(duenos)


EVS = [{'id': 'ev-de-otra'}, {'id': 'ev-libre'}]
app_d = AppConDuenos({'ev-de-otra': 'cita-vecina'})
check('se salta el que ya tiene dueño y toma el siguiente',
      (appmod.evento_sin_dueno(app_d, EVS, 'cita-mia') or {}).get('id'), 'ev-libre')
check('si todos tienen dueño, ninguno: se creará uno nuevo',
      appmod.evento_sin_dueno(AppConDuenos({'ev-de-otra': 'v1', 'ev-libre': 'v2'}),
                              EVS, 'cita-mia'), None)
check('el evento que ya es de LA MISMA cita sí se reconoce',
      (appmod.evento_sin_dueno(AppConDuenos({'ev-de-otra': 'cita-mia'}),
                               EVS, 'cita-mia') or {}).get('id'), 'ev-de-otra')
check('sin resultados de búsqueda, nada que adoptar',
      appmod.evento_sin_dueno(AppConDuenos({}), [], 'cita-mia'), None)
check('y lo que viene sin id se ignora',
      appmod.evento_sin_dueno(AppConDuenos({}), [{'summary': 'sin id'}], 'x'), None)

print('')
print('-- De una reunión que ya pasó no se avisa a nadie --')
# Una cita vieja que nunca llegó a subir sigue subiendo: el calendario tiene que
# contar lo que pasó. Lo que no se hace es mandar hoy la invitación de una
# reunión de hace cuatro meses.
from datetime import datetime, timedelta, timezone as _tz
ahora = datetime.now(_tz.utc)


def cuando(dias):
    ini = ahora + timedelta(days=dias)
    return {'start_time': ini.isoformat(),
            'end_time': (ini + timedelta(hours=1)).isoformat()}


check('la de la semana que viene avisa a todos',
      appmod.aviso_de_evento(cuando(7)), 'all')
check('la de hace cuatro meses, a nadie',
      appmod.aviso_de_evento(cuando(-120)), 'none')
check('la que empezó hace un rato y aún no acaba, también avisa',
      appmod.aviso_de_evento({'start_time': (ahora - timedelta(minutes=20)).isoformat(),
                              'end_time': (ahora + timedelta(minutes=40)).isoformat()}),
      'all')
check('sin fecha, se avisa: mejor de más que callarse',
      appmod.aviso_de_evento({}), 'all')
check('y con una fecha que no se entiende, igual',
      appmod.aviso_de_evento({'end_time': 'cuando sea'}), 'all')

print('')
print('-- A quien no se le puede invitar, no se le invita --')
# El campo de invitados no siempre trae correos: lo que llega de Atlas son
# NOMBRES. Google contestaba 400 «Invalid attendee email» y rechazaba el evento
# entero, así que la reunión no llegaba al calendario. Cinco citas llevaban así
# desde mayo, y el error no se escribía en ningún sitio.
ATLAS = {'invitados': 'CARMEN REINOSO, JOHANNA NIEVECELA', 'calendar_id': 'atlas'}
check('los nombres sueltos no van como invitados',
      appmod._build_attendees(ATLAS, {}, 'atlas.cenest@gmail.com'), [])
check('pero de una mezcla se rescata lo que sí es un correo',
      appmod._build_attendees({'invitados': 'Carmen Reinoso, esteban@ejemplo.com',
                               'calendar_id': 'atlas'}, {}, 'atlas.cenest@gmail.com'),
      [{'email': 'esteban@ejemplo.com'}])
check('y los correos de siempre siguen entrando',
      appmod._build_attendees({'invitados': 'a@b.com, c@d.ec', 'calendar_id': 'x'},
                              {}, 'org@x.com'),
      [{'email': 'a@b.com'}, {'email': 'c@d.ec'}])
check('lo que parece correo pero no lo es, fuera',
      appmod._build_attendees({'invitados': 'juan@casa, @nadie, sin arroba',
                               'calendar_id': 'x'}, {}, 'org@x.com'), [])

print('\n' + ('TODO CORRECTO' if not fallos else '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
