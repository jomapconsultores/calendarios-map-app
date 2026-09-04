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

print('\n' + ('TODO CORRECTO' if not fallos else '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
