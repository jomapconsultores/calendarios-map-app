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
  * que la misma reunión entre una vez por pasada hasta llenar el día;
  * que una reunión entre dos cuentas de la casa —que está en las dos agendas—
    se cuente como dos reuniones, que es lo que llenaba el calendario de citas
    repetidas;
  * que una repetición de una serie semanal se descarte por «ya la tengo»: todas
    comparten el mismo UID, y confundirlas es el error contrario.
"""
import sys, os
sys.path.insert(0, os.getcwd())

from app import agenda_entrante as ent
from app import marca_de_version
import email as _email

BUZON = 'buzon@ejemplo.com'


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
    def __init__(self, filas=(), fallo_lectura=None):
        self.filas = list(filas)
        self.insertados = []
        self.actualizados = []
        # Como falla PostgREST de verdad: no levanta excepción: contesta con un
        # error HTTP que el cliente traduce a lista vacía y un motivo.
        self.fallo_lectura = fallo_lectura

    def get(self, tabla, filtros=None, select=None):
        return list(self.filas)

    def get_q(self, tabla, params=None, select=None):
        # La agenda se lee por páginas (PostgREST corta en mil y no avisa): aquí
        # se respeta eso, para que el test recorra el mismo camino que producción.
        desde = int((params or {}).get('offset') or 0)
        hasta = desde + int((params or {}).get('limit') or len(self.filas) or 1)
        return list(self.filas)[desde:hasta]

    def get_q_detallado(self, tabla, params=None, select=None):
        if self.fallo_lectura:
            return [], self.fallo_lectura
        return self.get_q(tabla, params, select), None

    def get_todo(self, tabla, select=None, filters=None, pagina=1000, tope=100000):
        return self.get_todo_detallado(tabla, select, filters, pagina, tope)[0]

    def get_todo_detallado(self, tabla, select=None, filters=None,
                           pagina=1000, tope=100000):
        filas, desde = [], 0
        while True:
            lote, motivo = self.get_q_detallado(
                tabla, {'offset': desde, 'limit': pagina}, select)
            if motivo:
                return filas, motivo
            filas.extend(lote)
            if len(lote) < pagina:
                return filas, None
            desde += pagina

    def insert(self, tabla, data):
        self.insertados.append(data)
        return [{'id': f'nueva-{len(self.insertados)}'}]

    def update(self, tabla, id_val, data, id_col='id'):
        self.actualizados.append((id_val, data))
        return True


class AppFalsa:
    def __init__(self, filas=(), eventos=(), falla=None, fallo_lectura=None):
        self.supabase = SupabaseFalso(filas, fallo_lectura)
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
           estado='confirmed', lugar='Sala grande', uid=None, **extra):
    ev = {
        'id': id_, 'status': estado, 'updated': updated,
        # El nombre que el evento conserva en la agenda de cada invitado. Es lo
        # que permite reconocer la misma reunión vista desde otra cuenta.
        'iCalUID': uid or (id_ + '@google.com'),
        'summary': titulo, 'location': lugar,
        'start': {'dateTime': inicio}, 'end': {'dateTime': fin},
        'organizer': {'email': 'otro@ejemplo.com', 'displayName': 'OTRO DESPACHO'},
        'attendees': [{'email': CUENTA, 'self': True},
                      {'email': 'tercero@ejemplo.com'}],
    }
    ev.update(extra)
    return ev


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
        'google_account': CUENTA, 'google_updated': updated,
        'external_uid': 'ev-1@google.com',
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
check('y a su UID, que es lo que la identifica en las demás agendas',
      nueva['external_uid'], 'ev-nuevo@google.com')
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


# ------------------------------------ la misma reunión vista por dos cuentas
#
# Una reunión del despacho con Atlas está en las dos agendas, porque la una
# invitó a la otra. Es un compromiso, no dos. Reconociéndola sólo por la pareja
# (cuenta, evento), la copia de la segunda cuenta era siempre una reunión nueva:
# es lo que llenaba el calendario de la misma cita repetida.
print('\n-- La reunión que ya entró por OTRA cuenta de la casa --')
OTRA = 'atlas@ejemplo.com'
app = AppFalsa(filas=[cita()],                      # ya está, traída por jomap
               eventos=[evento('ev-1')])            # y ahora se ve desde atlas
enchufar_google(app)
res = ent.sincronizar_google(app, [OTRA], {OTRA: 'cal-atlas'})
check('no entra por segunda vez', (res[OTRA]['nuevas'], len(app.supabase.insertados)),
      (0, 0))
check('se dice que se reconoció, no que no pasó nada', res[OTRA]['copias'], 1)
check('y la fila sigue siendo de la cuenta que la trajo',
      len(app.supabase.actualizados), 0)

print('\n-- Lo mismo, cuando Google le cambia el id a la copia --')
# Entre dominios distintos la copia puede llevar otro identificador. Lo que no
# cambia nunca es el UID de calendario: es el que tiene que salvar el caso.
app = AppFalsa(filas=[cita()],
               eventos=[evento('otro-id-de-la-copia', uid='ev-1@google.com')])
enchufar_google(app)
res = ent.sincronizar_google(app, [OTRA], {OTRA: 'cal-atlas'})
check('tampoco entra dos veces',
      (res[OTRA]['nuevas'], res[OTRA]['copias']), (0, 1))

print('\n-- A una cita vieja, sin UID, se le pone el que le falta --')
# Es justo lo que permitía que volviera a entrar por otra cuenta.
app = AppFalsa(filas=[cita(external_uid=None)], eventos=[evento('ev-1')])
enchufar_google(app)
ent.sincronizar_google(app, [CUENTA], CAL)
check('se le guarda, y nada más',
      app.supabase.actualizados, [('cita-1', {'external_uid': 'ev-1@google.com'})])


# ------------------------------------------------ las repeticiones de una serie
print('\n-- Una reunión semanal: tres repeticiones --')
# Las tres llevan el MISMO iCalUID. Si se las confunde por eso, de una reunión
# semanal entra sólo la primera semana, que es el error contrario al de duplicar.
serie = [evento('base_20260920T200000Z', uid='base@google.com',
                recurringEventId='base',
                originalStartTime={'dateTime': '2026-09-20T15:00:00-05:00'}),
         evento('base_20260927T200000Z', uid='base@google.com',
                recurringEventId='base', inicio='2026-09-27T15:00:00-05:00',
                fin='2026-09-27T16:00:00-05:00',
                originalStartTime={'dateTime': '2026-09-27T15:00:00-05:00'}),
         evento('base_20261004T200000Z', uid='base@google.com',
                recurringEventId='base', inicio='2026-10-04T15:00:00-05:00',
                fin='2026-10-04T16:00:00-05:00',
                originalStartTime={'dateTime': '2026-10-04T15:00:00-05:00'})]
app = AppFalsa(filas=[], eventos=serie)
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('entran las tres', res[CUENTA]['nuevas'], 3)
check('y cada una con su propio nombre, no todas con el de la serie',
      len({c['external_uid'] for c in app.supabase.insertados}), 3)

print('\n-- Y en la siguiente pasada no vuelven a entrar --')
filas = [cita(id_=f'cita-{i}', google_event_id=e['id'],
              external_uid=ent.identidad_de_evento(e), start_time=e['start']['dateTime'],
              end_time=e['end']['dateTime'])
         for i, e in enumerate(serie)]
app = AppFalsa(filas=filas, eventos=serie)
enchufar_google(app)
res = ent.sincronizar_google(app, [CUENTA], CAL)
check('ninguna se duplica',
      (res[CUENTA]['nuevas'], res[CUENTA]['copias']), (0, 0))


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

def indice(filas=()):
    ind = ent._indice_vacio()
    for f in filas:
        ent._apuntar(ind, f)
    return ind


def contador():
    return {'nuevas': 0, 'actualizadas': 0, 'canceladas': 0, 'copias': 0}


app = AppFalsa()
res = contador()
ent._aplicar_invitacion(app, LECTURA, 'csccue@ejemplo.gob.ec', indice(), res)
check('entra a la agenda como una cita más', res['nuevas'], 1)
check('atada a su UID, que es su identificador allí',
      app.supabase.insertados[0]['external_uid'], 'uid-123')
check('y marcada como venida de fuera',
      app.supabase.insertados[0]['origen'], 'externo')

print('\n-- La misma invitación reenviada --')
YA_ESTA = {**LECTURA['cita'], 'id': 'cita-x', 'status': 'confirmed',
           'external_uid': 'uid-123'}
app = AppFalsa()
res = contador()
ent._aplicar_invitacion(app, LECTURA, 'csccue@ejemplo.gob.ec', indice([YA_ESTA]), res)
check('no entra dos veces ni se toca',
      (res['nuevas'], res['actualizadas'], len(app.supabase.actualizados)), (0, 0, 0))

print('\n-- Cambian la hora y reenvían --')
app = AppFalsa()
movida = {**LECTURA, 'secuencia': 1,
          'cita': {**LECTURA['cita'], 'start_time': '2026-09-16T13:00:00+00:00'}}
res = contador()
ent._aplicar_invitacion(app, movida, 'csccue@ejemplo.gob.ec', indice([YA_ESTA]), res)
check('se actualiza la que había, no se crea otra',
      (res['actualizadas'], res['nuevas']), (1, 0))
check('con la hora nueva',
      app.supabase.actualizados[0][1]['start_time'], '2026-09-16T13:00:00+00:00')

print('\n-- Llega la cancelación --')
app = AppFalsa()
res = contador()
ent._aplicar_invitacion(app, {**LECTURA, 'cancelado': True},
                        'csccue@ejemplo.gob.ec', indice([YA_ESTA]), res)
check('la cita queda cancelada aquí también',
      (res['canceladas'], app.supabase.actualizados[0][1]['status']),
      (1, 'cancelled'))


# ------------------------------- nuestra propia invitación, de vuelta a casa
#
# La plataforma convoca desde csccue e invita a hotmail, que también es cuenta
# de la casa. Esa invitación llega a la bandeja de hotmail con csccue como quien
# convoca —así que no es «lo que yo mismo apunté»— y volvía a entrar como una
# reunión nueva. La misma reunión, dos veces: la que se creó y la que se recibió
# de sí misma. Se reconoce por el UID, que lo pone esta plataforma y lleva dentro
# el número de la cita.
print('\n-- La invitación de una reunión que ya lleva la API de Google --')
# Si los dos caminos escriben sobre la misma fila, se corrigen el uno al otro
# cada cuarto de hora y piden cada vez que alguien lo mire. Manda el que tiene el
# evento; el correo, ahí, es la copia.
app = AppFalsa()
res = contador()
ent._aplicar_invitacion(app, {**LECTURA, 'secuencia': 2,
                              'cita': {**LECTURA['cita'], 'title': 'Otro título'}},
                        'csccue@ejemplo.gob.ec',
                        indice([{**YA_ESTA, 'google_event_id': 'ev-9'}]), res)
check('no se pisan entre ellos',
      (res['actualizadas'], len(app.supabase.actualizados)), (0, 0))

print('\n-- La invitación que mandamos nosotros, recibida en otra cuenta nuestra --')
NUESTRA = {**LECTURA['cita'], 'id': 'cita-del-despacho', 'status': 'confirmed',
           'external_uid': None}
propia = {**LECTURA, 'uid': 'cita-cita-del-despacho@calendario.map'}
app = AppFalsa()
res = contador()
ent._aplicar_invitacion(app, propia, 'maposligua@hotmail.com', indice([NUESTRA]), res)
check('no se convierte en una segunda cita',
      (res['nuevas'], len(app.supabase.insertados)), (0, 0))
check('se reconoce como la que ya teníamos', res['copias'], 1)
check('y se le guarda el UID para reconocerla sin pensar la próxima vez',
      app.supabase.actualizados,
      [('cita-del-despacho', {'external_uid': 'cita-cita-del-despacho@calendario.map'})])

print('')
print('-- La base no contesta cuando se le pregunta qué hay --')
# El caso que llenaba el calendario sin que fallara nada a la vista: PostgREST
# contesta con un error —o le falta una columna del select, que es una migración
# sin aplicar— y devuelve []. Tomar ese [] por «la agenda está vacía» hace que
# cada reunión leída se dé por nueva y se vuelva a crear la agenda entera.
for motivo in ('red', 'columna', 'rechazo'):
    app = AppFalsa(filas=[], eventos=[evento('ev-1')], fallo_lectura=motivo)
    enchufar_google(app)
    r = ent.sincronizar_google(app, [CUENTA], CAL)
    cuenta = r[CUENTA]
    check('con «%s» no se escribe nada' % motivo,
          (cuenta['nuevas'], len(app.supabase.insertados),
           len(app.supabase.actualizados)), (0, 0, 0))
    check('  y se dice por qué, en vez de callar', bool(cuenta.get('error')), True)

app = AppFalsa(filas=[], eventos=[evento('ev-1')], fallo_lectura='red')
enchufar_google(app)
r = ent.sincronizar_correo(app, ['buzon@ejemplo.com'])
# Que no inserte nada no prueba nada aquí —sin buzón tampoco insertaría—, así
# que se mira que la pasada se detenga POR ESTO y lo diga.
check('lo mismo con las invitaciones que llegan por correo',
      r['buzon@ejemplo.com'].get('error'), 'no se pudo leer la agenda de aquí')

print('')
print('-- Una serie semanal que llega por correo --')
# Todas las semanas de una serie comparten UID; lo que dice de cuál se trata es
# el RECURRENCE-ID. Sin mirarlo, la invitación de la semana que viene se tomaba
# por la de esta —misma cita, misma fila— y le cambiaba la hora en vez de entrar
# como otra reunión.
CRLF = chr(13) + chr(10)


def correo_ics(*lineas):
    ics = CRLF.join(('BEGIN:VCALENDAR', 'METHOD:REQUEST', 'BEGIN:VEVENT',
                     'UID:serie-semanal@outlook.com',
                     'SUMMARY:Comite semanal',
                     'ORGANIZER:mailto:quien@convoca.com') + lineas +
                    ('END:VEVENT', 'END:VCALENDAR', ''))
    cabecera = CRLF.join(('From: quien@convoca.com', 'Subject: Invitacion',
                          'Content-Type: text/calendar; method=REQUEST',
                          'MIME-Version: 1.0', '', ''))
    return _email.message_from_string(cabecera + ics)


primera = ent.leer_invitacion(correo_ics(
    'DTSTART:20260915T150000Z', 'DTEND:20260915T160000Z'), BUZON, 'cal-1')
otra = ent.leer_invitacion(correo_ics(
    'DTSTART:20260922T150000Z', 'DTEND:20260922T160000Z',
    'RECURRENCE-ID:20260922T150000Z'), BUZON, 'cal-1')
check('la primera se apunta con el UID de la serie',
      primera['uid'], 'serie-semanal@outlook.com')
check('y la semana siguiente NO se llama igual', otra['uid'] != primera['uid'], True)

app = AppFalsa()
res = contador()
ind = indice()
ent._aplicar_invitacion(app, primera, BUZON, ind, res)
ent._aplicar_invitacion(app, otra, BUZON, ind, res)
check('entran las dos semanas, no una pisando a la otra',
      (res['nuevas'], len(app.supabase.insertados)), (2, 2))
check('y cada una a su hora',
      [c['start_time'][:10] for c in app.supabase.insertados],
      ['2026-09-15', '2026-09-22'])

# La misma invitacion repetida sigue sin entrar dos veces.
ent._aplicar_invitacion(app, otra, BUZON, ind, res)
check('y la repetición reenviada tampoco se duplica', len(app.supabase.insertados), 2)

print('')
print('-- La misma repetición, vista por los tres caminos --')
# El duplicado de manual: cada camino escribía el día a su manera —Google en
# hora local, el correo en ISO, la plataforma con la Z de la API— y la misma
# repetición quedaba archivada bajo tres nombres distintos.
por_google = ent.identidad_de_evento({
    'iCalUID': 'SERIE@google.com', 'recurringEventId': 'serie',
    'originalStartTime': {'dateTime': '2026-09-22T10:00:00-05:00'}})
al_crearla = marca_de_version({
    'updated': '2026-09-11T10:00:00.000Z', 'iCalUID': 'SERIE@google.com',
    'recurringEventId': 'serie',
    'originalStartTime': {'dateTime': '2026-09-22T15:00:00Z'}})['external_uid']
por_correo = ent.identidad('SERIE@google.com', '2026-09-22T15:00:00+00:00')
check('los tres escriben el mismo nombre',
      (al_crearla, por_correo), (por_google, por_google))
check('con el día pegado, para no confundir una semana con otra',
      por_google, 'serie@google.com#20260922T150000Z')
check('y un evento suelto se queda con su UID a secas',
      ent.identidad_de_evento({'iCalUID': 'SUELTO@google.com'}),
      'suelto@google.com')

print('')
print('-- La reunión que convocamos nosotros, de vuelta por la bandeja de otra cuenta --')
# El caso real que se quedó dando vueltas en producción: la plataforma crea el
# evento en la agenda de una cuenta, y la invitación llega por correo a la cuenta
# de Microsoft invitada. Google pone de UID el identificador del evento con
# '@google.com' detrás, así que la fila que ya existe se reconoce por ahí: una
# tenía el evento y ningún UID, la otra el UID y ningún evento.
YA_CREADA = {**LECTURA['cita'], 'id': 'cita-en-google', 'status': 'confirmed',
             'google_event_id': 'abc123', 'external_uid': None,
             'google_account': 'jomap@ejemplo.com'}
de_vuelta = {**LECTURA, 'uid': 'abc123@google.com'}
app = AppFalsa()
res = contador()
ind = indice([YA_CREADA])
ent._aplicar_invitacion(app, de_vuelta, BUZON, ind, res)
check('no entra como una segunda cita',
      (res['nuevas'], len(app.supabase.insertados)), (0, 0))
check('se reconoce como la que ya teníamos', res['copias'], 1)
check('y se le guarda el UID, para que la próxima vez sea inmediato',
      app.supabase.actualizados,
      [('cita-en-google', {'external_uid': 'abc123@google.com'})])

# Y una repetición de esa serie: el UID lleva el día pegado, y lo que nombra al
# evento sigue siendo lo de delante.
check('también con el día pegado',
      ent._evento_de_google('abc123@google.com#20260922T150000Z'), 'abc123')
check('y un UID que no es de Google no nombra ningún evento',
      ent._evento_de_google('uid-de-outlook@outlook.com'), None)

print('\n' + ('TODO CORRECTO' if not fallos else
              '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
