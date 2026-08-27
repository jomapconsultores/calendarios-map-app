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


class AppFalsa:
    supabase = SupabaseFalso()


CALENDARIOS = [
    {'calendar_id': 'cal-a', 'name': 'JOMAP', 'email': 'jomap@ejemplo.com',
     'color': '#4f46e5', 'google_cal_id': 'gcal-a'},
    {'calendar_id': 'cal-b', 'name': 'CSCCUE', 'email': 'csccue@ejemplo.com',
     'color': '#16a34a', 'google_cal_id': 'gcal-b'},
]

CITA = {
    'id': 'cita-1', 'calendar_id': 'cal-a',
    'google_event_id': 'ev-viejo', 'google_cal_id': 'gcal-a',
    'status': 'confirmed', 'title': 'REUNIÓN', 'encargado': 'MARCO',
    'tema': 'Revisión anual', 'client_name': 'CLIENTE', 'client_email': 'c@ejemplo.com',
    'start_time': '2026-09-01T15:00:00+00:00', 'end_time': '2026-09-01T16:00:00+00:00',
    'invitados': 'invitado@ejemplo.com', 'lugar': 'OFICINA', 'direccion': 'Calle 1',
    'ciudad': 'CUENCA', 'mapa': '', 'notes': '', 'meeting_link': '',
}


def preparar(falla_en=()):
    """Enchufa el Google de mentira y devuelve su registro de llamadas."""
    reg = Registro()
    appmod.get_google_creds = lambda app: 'credenciales-de-mentira'
    appmod.build = lambda *a, **k: ServicioFalso(reg, falla_en)
    appmod._get_calendar_config = lambda app: CALENDARIOS
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

print('\n-- Cambiar la cita de calendario --')
reg = preparar()
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'calendar_id': 'cal-b'})
check('borra el del calendario anterior', len(reg.solo('delete')), 1)
check('del calendario del que salía', reg.solo('delete')[0]['calendarId'], 'gcal-a')
check('y crea en el nuevo', reg.solo('insert')[0]['calendarId'], 'gcal-b')
check('los dos avisando', [reg.solo('delete')[0]['sendUpdates'],
                           reg.solo('insert')[0]['sendUpdates']], ['all', 'all'])
check('se guarda el identificador nuevo', parches,
      {'google_event_id': 'evento-nuevo-123', 'google_cal_id': 'gcal-b'})

print('\n-- Cuando Google falla --')
reg = preparar(falla_en={'update': 'boom'})
parches, aviso = appmod._reflejar_en_google(
    AppFalsa(), CITA, {'start_time': '2026-09-04T15:00:00+00:00'})
check('se avisa de que el correo se quedó atrás', bool(aviso and 'no se pudo actualizar' in aviso), True)
check('y no se inventan identificadores', parches, {})

reg = preparar(falla_en={'insert': 'boom'})
parches, aviso = appmod._reflejar_en_google(AppFalsa(), CITA, {'calendar_id': 'cal-b'})
check('mudanza a medias: el identificador se limpia para poder repararlo',
      parches, {'google_event_id': None, 'google_cal_id': None})
check('y se dice que hay que reparar', bool(aviso and 'Reparar eventos' in aviso), True)

print('\n-- Sin Google conectado --')
appmod.get_google_creds = lambda app: None
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
