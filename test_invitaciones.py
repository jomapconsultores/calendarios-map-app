# -*- coding: utf-8 -*-
"""Prueba de la cita que viaja por correo, en los dos sentidos.

Sin salir a la red ni tocar ninguna cuenta: se construye la invitación y se
mira lo que dice, y se le da a leer un correo de invitación de mentira para
comprobar qué entiende de él.

Lo que se vigila es lo que rompe de verdad cuando falla:

  * el .ics tiene que ser un .ics — si una línea se pasa de 75 octetos sin
    plegar, o un punto y coma va sin escapar, el que lo recibe ve una cita a
    medias o no ve ninguna;
  * el UID no puede cambiar entre la convocatoria y su cambio de hora, o el
    otro acaba con dos reuniones donde había una;
  * la cuenta que convoca no se invita a sí misma;
  * de la bandeja se saca el evento de verdad, y lo que uno mismo convocó no
    vuelve a entrar como si se lo hubieran agendado.
"""
import sys, os
sys.path.insert(0, os.getcwd())

from app import invitaciones as inv
from app import agenda_entrante as ent


fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


CITA = {
    'id': 'cita-99', 'calendar_id': 'cal-ms',
    'title': 'REUNIÓN; ANUAL', 'encargado': 'MARCO',
    'tema': 'Presupuesto, cierre y otros',
    'client_name': 'CLIENTE', 'client_email': 'cliente@ejemplo.com',
    'start_time': '2026-09-10T14:00:00+00:00',
    'end_time':   '2026-09-10T15:00:00+00:00',
    'invitados': 'uno@ejemplo.com, dos@ejemplo.com',
    'lugar': 'OFICINA', 'direccion': 'Calle 1', 'ciudad': 'CUENCA',
    'mapa': '', 'notes': 'Traer el informe', 'meeting_link': '',
}
CUENTA = 'csccue@ejemplo.gob.ec'
EMAIL_MAP = {'cal-ms': CUENTA}


# ---------------------------------------------------------------- la que sale
print('\n-- A quién se invita --')
destinos = inv.destinatarios_de(CITA, EMAIL_MAP, CUENTA)
check('la cuenta que convoca no se invita a sí misma', CUENTA in destinos, False)
check('van los invitados de la ficha y el cliente',
      sorted(destinos),
      ['cliente@ejemplo.com', 'dos@ejemplo.com', 'uno@ejemplo.com'])
check('nadie repetido', len(destinos), len(set(destinos)))

print('\n-- El archivo de calendario --')
ics = inv.construir_ics(CITA, CUENTA, destinos)
lineas = ics.split('\r\n')

check('se anuncia como convocatoria', 'METHOD:REQUEST' in lineas, True)
check('abre y cierra el calendario',
      (lineas[0], 'END:VCALENDAR' in lineas), ('BEGIN:VCALENDAR', True))
check('lleva un evento dentro',
      ('BEGIN:VEVENT' in lineas, 'END:VEVENT' in lineas), (True, True))
check('la hora va en UTC, sin depender de zonas',
      'DTSTART:20260910T140000Z' in lineas, True)
check('y termina cuando toca', 'DTEND:20260910T150000Z' in lineas, True)
check('quien convoca es la cuenta que corresponde',
      f'ORGANIZER;CN={CUENTA}:mailto:{CUENTA}' in lineas, True)
# Las líneas de invitado se pasan de 75 octetos y van plegadas: para leerlas
# hay que deshacer el plegado, igual que hace el cliente de correo del otro
# lado. Comprobarlas sin desplegar sería comprobar otra cosa.
enteras = ent._desplegar(ics).split('\r\n')
check('cada invitado puede responder',
      sum(1 for l in enteras if l.startswith('ATTENDEE') and 'RSVP=TRUE' in l),
      len(destinos))
check('y son exactamente los invitados, sin colarse el organizador',
      sorted(l.split('mailto:')[1] for l in enteras if l.startswith('ATTENDEE')),
      sorted(destinos))

# El punto y coma del título parte el campo en dos si no va escapado: el que
# recibe la invitación vería «REUNIÓN» y perdería el resto.
resumen = [l for l in lineas if l.startswith('SUMMARY')][0]
check('el punto y coma del título va escapado', r'REUNIÓN\;' in resumen, True)

largas = [l for l in lineas if len(l.encode('utf-8')) > 75]
check('ninguna línea se pasa de 75 octetos', largas, [])

print('\n-- El mismo evento sigue siendo el mismo --')
uid_original = inv.uid_de_la_cita(CITA)
movida = dict(CITA, start_time='2026-09-11T14:00:00+00:00')
ics2 = inv.construir_ics(movida, CUENTA, destinos, secuencia=1)
check('el identificador no cambia al mover la cita',
      inv.uid_de_la_cita(movida), uid_original)
check('pero la versión sube, para que sustituya a la anterior',
      'SEQUENCE:1' in ics2.split('\r\n'), True)
check('y la fecha es la nueva',
      'DTSTART:20260911T140000Z' in ics2.split('\r\n'), True)

print('\n-- Retirarla --')
cancelacion = inv.construir_ics(CITA, CUENTA, destinos, metodo='CANCEL', secuencia=2)
lineas_c = cancelacion.split('\r\n')
check('se anuncia como cancelación', 'METHOD:CANCEL' in lineas_c, True)
check('y el evento queda como cancelado', 'STATUS:CANCELLED' in lineas_c, True)
check('sobre el mismo identificador de siempre',
      f'UID:{uid_original}' in lineas_c, True)


# --------------------------------------------------------------- la que entra
def correo_con_invitacion(cuerpo_ics, de='convocante@ejemplo.com'):
    from email.message import EmailMessage
    m = EmailMessage()
    m['From'] = de
    m['Subject'] = 'Invitación'
    m.set_content('Le invitamos a la reunión.')
    m.add_attachment(cuerpo_ics.encode('utf-8'), maintype='text',
                     subtype='calendar', filename='invite.ics')
    return m


ICS_QUE_LLEGA = '\r\n'.join([
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'METHOD:REQUEST', 'BEGIN:VEVENT',
    'UID:convocatoria-123@otro.sistema',
    'DTSTART:20260915T130000Z',
    'DTEND:20260915T140000Z',
    'SUMMARY:Sesión ordinaria del pleno',
    'DESCRIPTION:Primer punto\\nSegundo punto',
    'LOCATION:Sala 3\\, segundo piso',
    'ORGANIZER;CN=Secretaría:mailto:secretaria@ejemplo.gob.ec',
    'END:VEVENT', 'END:VCALENDAR'])

print('\n-- Lo que llega a la bandeja --')
fila = ent._fila_desde_mensaje(correo_con_invitacion(ICS_QUE_LLEGA), CUENTA, 'cal-ms')
check('se reconoce como algo agendado', bool(fila), True)
check('con su identificador, para no duplicarlo en cada pasada',
      fila['event_id'], 'convocatoria-123@otro.sistema')
check('el título llega entero', fila['titulo'], 'Sesión ordinaria del pleno')
check('la coma del lugar se desescapa', fila['lugar'], 'Sala 3, segundo piso')
check('el salto de línea de la descripción también',
      fila['descripcion'], 'Primer punto\nSegundo punto')
check('se sabe quién convoca', fila['organizador'], 'secretaria@ejemplo.gob.ec')
check('y en qué cuenta entró', (fila['cuenta_email'], fila['calendar_id']),
      (CUENTA, 'cal-ms'))
check('la fecha se guarda en formato comparable',
      fila['start_time'].startswith('2026-09-15T13:00:00'), True)
check('está vigente', fila['estado'], 'activo')

print('\n-- Una cancelación que llega --')
cancelada = ICS_QUE_LLEGA.replace('METHOD:REQUEST', 'METHOD:CANCEL')
fila_c = ent._fila_desde_mensaje(correo_con_invitacion(cancelada), CUENTA, 'cal-ms')
check('se marca como cancelada, no se borra en silencio',
      fila_c['estado'], 'cancelado')
check('sobre el mismo identificador, para que sustituya a la que había',
      fila_c['event_id'], 'convocatoria-123@otro.sistema')

print('\n-- Lo que no es una invitación --')
from email.message import EmailMessage
suelto = EmailMessage()
suelto['From'] = 'alguien@ejemplo.com'
suelto.set_content('Buenos días, ¿tiene un rato el martes?')
check('un correo normal no entra en la agenda',
      ent._fila_desde_mensaje(suelto, CUENTA, 'cal-ms'), None)

propia = correo_con_invitacion(ICS_QUE_LLEGA.replace(
    'mailto:secretaria@ejemplo.gob.ec', f'mailto:{CUENTA}'), de=CUENTA)
check('lo que uno mismo convoca no vuelve como «me lo agendaron»',
      ent._fila_desde_mensaje(propia, CUENTA, 'cal-ms'), None)

print('\n-- Una cita vieja cuyo calendario cambió de cuenta --')
# Se creó cuando ese calendario agendaba por Google: el evento EXISTE allí. Que
# hoy ese calendario mande por correo no borra el evento de ayer.
import app as appmod


class _AppFalsa:
    class supabase:
        @staticmethod
        def get(tabla, filtros=None, select=None): return []
        @staticmethod
        def update(tabla, id_val, data, id_col='id'): return True


borrados = []
appmod._get_calendar_config = lambda app: [
    {'calendar_id': 'cal-ms', 'name': 'CSCCUE', 'email': CUENTA,
     'google_cal_id': None, 'cuenta_email': CUENTA, 'proveedor': 'microsoft'}]
appmod._borrar_evento_google = lambda app, apt, avisar=True: borrados.append(apt) or True

vieja = dict(CITA, google_event_id='ev-de-antes', google_cal_id='gcal-viejo',
             google_account='mposligua0000@gmail.com')
check('se borra el evento de Google que sí existe',
      (appmod.retirar_de_la_agenda(_AppFalsa(), vieja), len(borrados)), (True, 1))

del borrados[:]
nueva = dict(CITA, google_event_id=None, google_cal_id=None, google_account=None)
appmod._invitaciones.enviar_cancelacion = lambda *a, **k: (1, None)
check('y la que nunca estuvo en Google se retira por correo',
      (appmod.retirar_de_la_agenda(_AppFalsa(), nueva), len(borrados)), (True, 0))

print('\n-- Fechas de día completo --')
todo_el_dia = ICS_QUE_LLEGA.replace(
    'DTSTART:20260915T130000Z', 'DTSTART;VALUE=DATE:20260915').replace(
    'DTEND:20260915T140000Z', 'DTEND;VALUE=DATE:20260916')
fila_d = ent._fila_desde_mensaje(correo_con_invitacion(todo_el_dia), CUENTA, 'cal-ms')
check('se reconocen como tales', fila_d['todo_el_dia'], True)
check('y ocupan el día entero, no una hora suelta',
      fila_d['start_time'].startswith('2026-09-15T00:00:00'), True)

print('\n' + ('TODO CORRECTO' if not fallos else
              '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
