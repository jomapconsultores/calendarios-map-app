# -*- coding: utf-8 -*-
"""Prueba del correo: cómo se lee un mensaje, cómo se guarda y con qué se ata.

Sin red y sin cuentas: los mensajes se construyen aquí y la base va a una
carpeta temporal.

Lo que se vigila:

  * que el mismo correo visto en dos cuentas sea UNA fila con dos copias — las
    cuentas de la casa se escriben entre ellas, y sin esto cada hilo interno
    saldría dos o tres veces;
  * que un número de Quipux recogido se ate como seguro, y uno que sólo tiene
    la forma, como dudoso y sólo si el correo habla de oficios;
  * que la invitación de calendario se reconozca por su UID, y la cancelación
    como cancelación;
  * que los avisos de la propia plataforma no se tomen por compromisos;
  * que al recalcular los vínculos no se pierda el de la cita, que no se
    puede volver a calcular porque la invitación no se guarda;
  * y que lo marcado y lo sin leer se refresquen en cada pasada —lo leído en
    el teléfono deja de estar pendiente—, que lo marcado de antes se traiga, y
    que lo pendiente que sube a la plataforma no incluya boletines ni lo que
    escribe la propia casa.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

from email.message import EmailMessage

from correo import almacen, lectura, publicar, relacion

fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


BD = os.path.join(tempfile.mkdtemp(prefix='correo_'), 'prueba.db')

ICS = ('BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:ABC123@google.com\r\n'
       'SUMMARY:Reunión de\r\n  seguimiento\r\nDTSTART;TZID=America/Guayaquil:20261020T093000\r\n'
       'END:VEVENT\r\nEND:VCALENDAR\r\n')


def mensaje(asunto, cuerpo, de='Ana Pérez <ana@cuenca.gob.ec>', mid='<m1@x>', ics=None,
            html=False, adjunto=None):
    m = EmailMessage()
    m['From'] = de
    m['To'] = 'marcoposligua@csccue.gob.ec'
    m['Subject'] = asunto
    m['Date'] = 'Mon, 05 Oct 2026 10:00:00 -0500'
    if mid:
        m['Message-ID'] = mid
    if html:
        m.set_content('<p>' + cuerpo + '</p>', subtype='html')
    else:
        m.set_content(cuerpo)
    if ics:
        m.add_attachment(ics.encode(), maintype='text', subtype='calendar',
                         filename='invite.ics')
    if adjunto:
        m.add_attachment(b'%PDF', maintype='application', subtype='pdf', filename=adjunto)
    return m.as_bytes()


print('\nLectura de un mensaje')
m = lectura.leer(mensaje('Matriz', 'Remito la matriz.', adjunto='matriz.xlsx', ics=ICS))
check('el asunto, decodificado', m['asunto'], 'Matriz')
check('la dirección del remitente, sola y en minúsculas', m['de_correo'], 'ana@cuenca.gob.ec')
check('la fecha, en ISO con zona', m['fecha'], '2026-10-05T10:00:00-05:00')
check('los adjuntos, por su nombre', m['adjuntos'], ['invite.ics', 'matriz.xlsx'])
check('la invitación queda aparte', 'BEGIN:VEVENT' in (m['ics'] or ''), True)
h = lectura.leer(mensaje('x', 'Hola<br>mundo &amp; más', html=True))
check('el HTML se lee como texto', h['texto'], 'Hola\nmundo & más')

print('\nRespuesta de un FETCH de varios mensajes')
resp = [(b'1 (UID 7 FLAGS (\\Seen) BODY[]<0> {3}', b'abc'), b')',
        (b'2 (UID 9 BODY[]<0> {3}', b'def'), b' FLAGS ())']
check('uid, sin leer y bytes de cada uno', lectura._trozos(resp),
      [(7, False, b'abc'), (9, True, b'def')])

print('\nNúmeros de Quipux')
check('las formas conocidas', sorted(relacion.RE_NUMERO.findall(
    'DGPG-2050-2026, CIUDADANO-CIU-2025-7061-TEMP y CCPD-C-SE-2026-0975-OF')),
    ['CCPD-C-SE-2026-0975-OF', 'CIUDADANO-CIU-2025-7061-TEMP', 'DGPG-2050-2026'])
conocidos = {'DGPG-2050-2026': {'asunto': 'Matriz de requerimientos',
                                'plazo_fecha': '2026-10-15', 'area': 'Planificación'}}
v = relacion.vinculos({'asunto': 'RE: dgpg-2050-2026', 'texto': 'Adjunto lo pedido.'},
                      conocidos)
check('un número recogido se ata como seguro',
      [(x['tipo'], x['clave'], x['seguro']) for x in v], [('quipux', 'DGPG-2050-2026', True)])
check('y lleva el asunto y el plazo del documento', v[0]['detalle'],
      'Matriz de requerimientos · plazo 2026-10-15 · Planificación')
v = relacion.vinculos({'asunto': 'Oficio DGPG-9999-2026', 'texto': ''}, conocidos)
check('uno sin recoger, como dudoso', [(x['clave'], x['seguro']) for x in v],
      [('DGPG-9999-2026', False)])
v = relacion.vinculos({'asunto': 'Norma ISO-9001-2015', 'texto': 'certificación'}, conocidos)
check('sin hablar de documentos, la forma sola no basta', v, [])

print('\nInvitaciones y plazos')
v = relacion.vinculos({'asunto': 'Invitación', 'texto': '', 'ics': ICS})
check('la cita, por su UID', [(x['tipo'], x['clave']) for x in v],
      [('cita', 'abc123@google.com')])
check('con su título desplegado y su hora', v[0]['detalle'],
      'Reunión de seguimiento · 2026-10-20 09:30')
v = relacion.vinculos({'asunto': 'x', 'texto': '', 'ics': ICS.replace('REQUEST', 'CANCEL')})
check('la cancelación se dice', v[0]['detalle'].startswith('CANCELADA'), True)
v = relacion.vinculos({'asunto': 'Informe', 'fecha': '2026-10-05T10:00:00-05:00',
                       'texto': 'Remitir hasta el 15 de octubre de 2026.',
                       'de_correo': 'ana@cuenca.gob.ec'})
check('un plazo escrito en el texto', [(x['tipo'], x['clave'], x['seguro']) for x in v],
      [('plazo', '2026-10-15', False)])
v = relacion.vinculos({'asunto': 'Informe', 'fecha': '2026-10-05T10:00:00-05:00',
                       'texto': 'Remitir hasta el 15 de octubre de 2026.',
                       'de_correo': 'yo@casa.com'}, propias=['yo@casa.com'])
check('el plazo que uno da no es uno que le dan', v, [])
v = relacion.vinculos({'asunto': 'Boletín', 'fecha': '2026-10-05', 'masivo': True,
                       'texto': 'Oficio DGPG-9999-2026, reporte hasta el 30 de noviembre de 2026'},
                      conocidos)
check('de un boletín no se sacan plazos ni números sin comprobar', v, [])
b = lectura.leer(mensaje('x', 'y').replace(b'Subject:', b'List-Unsubscribe: <mailto:u@x>\nSubject:'))
check('un boletín se reconoce por su cabecera', b['masivo'], True)
v = relacion.vinculos({'asunto': '📋 Tus tareas pendientes: 87 — 09/10/2026',
                       'fecha': '2026-10-09', 'texto': 'hasta el 20 de octubre de 2026'})
check('el aviso de la plataforma se aparta, sin plazo',
      [x['tipo'] for x in v], ['plataforma'])

print('\nAlmacén')
crudo = mensaje('RE: DGPG-2050-2026', 'Va el informe.', mid='<igual@x>', ics=ICS)
with almacen.abrir(BD) as db:
    for cuenta in ('atlas.cenest@gmail.com', 'marcoposligua@csccue.gob.ec'):
        leido = lectura.leer(crudo)
        mid, nuevo = almacen.guardar(db, leido, cuenta, 'INBOX', 5)
        if nuevo:
            almacen.guardar_vinculos(db, mid, relacion.vinculos(leido, conocidos))
    sin_id = lectura.leer(mensaje('sin id', 'x', mid=None))
    almacen.guardar(db, sin_id, 'atlas.cenest@gmail.com', 'INBOX', 6)
    almacen.guardar(db, sin_id, 'atlas.cenest@gmail.com', 'INBOX', 6)
res = almacen.resumen(BD)
check('el mismo correo en dos cuentas es una fila (más el que no trae id)', res['total'], 2)
encontrado = almacen.vinculados('quipux', 'dgpg-2050-2026', ruta=BD)
check('se encuentra por el número del quipux', len(encontrado), 1)
check('con las dos cuentas donde está', encontrado[0]['cuentas'],
      ['atlas.cenest@gmail.com', 'marcoposligua@csccue.gob.ec'])
check('se busca por texto', len(almacen.buscar('informe', ruta=BD)), 1)
check('se busca por cuenta', len(almacen.buscar(cuenta='atlas.cenest@gmail.com', ruta=BD)), 2)

with almacen.abrir(BD) as db:
    almacen.guardar_vinculos(db, encontrado[0]['id'], [], tipos=('quipux', 'plazo'))
tipos = sorted(v['tipo'] for v in almacen.mensaje(encontrado[0]['id'], BD)['vinculos'])
check('recalcular el quipux no borra la cita', tipos, ['cita'])

with almacen.abrir(BD) as db:
    almacen.apuntar_marca(db, 'a@x', 'INBOX', 11, 40)
    almacen.apuntar_marca(db, 'a@x', 'INBOX', 11, 55)
    check('la marca de la carpeta avanza', almacen.marca(db, 'a@x', 'INBOX'), (11, 55))

print('\nMarcados y sin leer')


class ImapDeMentira:
    """Lo justo de imaplib para `refrescar_banderas`: SEARCH y FETCH por UID."""
    def __init__(self, mensajes, sin_leer, marcados, contestados=()):
        self.mensajes = mensajes
        self.banderas = {'UNSEEN': set(sin_leer), 'FLAGGED': set(marcados),
                         'ANSWERED': set(contestados)}
        self.pedidos = []

    def uid(self, orden, *args):
        if orden == 'SEARCH':
            return 'OK', [' '.join(str(u) for u in sorted(self.banderas[args[1]])).encode()]
        uids = [int(u) for u in args[0].split(',')]
        self.pedidos += uids
        resp = []
        for u in uids:
            resp += [(f'1 (UID {u} FLAGS (\\Flagged) BODY[]<0> {{3}}'.encode(),
                      self.mensajes[u]), b')']
        return 'OK', resp


BD2 = os.path.join(tempfile.mkdtemp(prefix='correo_'), 'estado.db')
CASA = 'yo@casa.com'
HOY = date.today()
reciente = (HOY - timedelta(days=2)).strftime('%a, %d %b %Y 10:00:00 -0500')
viejo = (HOY - timedelta(days=200)).strftime('%a, %d %b %Y 10:00:00 -0500')


def con_fecha(crudo, cuando):
    return crudo.replace(b'Mon, 05 Oct 2026 10:00:00 -0500', cuando.encode())


buzon = {
    1: con_fecha(mensaje('Revisar contrato', 'Por favor revisa.', mid='<c1@x>'), reciente),
    2: con_fecha(mensaje('Hola', 'ya está', de=CASA, mid='<c2@x>'), reciente),
    3: con_fecha(mensaje('Oferta', 'compra', mid='<c3@x>'), reciente).replace(
        b'Subject:', b'List-Unsubscribe: <mailto:u@x>\nSubject:'),
    4: con_fecha(mensaje('Viejo marcado', 'pendiente de hace meses', mid='<c4@x>'), viejo),
    5: con_fecha(mensaje('Pregunta', 'contesta', mid='<c5@x>'), reciente),
}
with almacen.abrir(BD2) as db:
    for uid in (1, 2, 3, 5):
        almacen.guardar(db, lectura.leer(buzon[uid]), 'b@gmail.com', 'Todos', uid)
    respuesta = con_fecha(mensaje('RE: Pregunta', 'listo', de=CASA, mid='<r5@x>'), reciente)
    leido = lectura.leer(respuesta.replace(b'Subject:', b'In-Reply-To: <c5@x>\nSubject:'))
    almacen.guardar(db, leido, CASA, 'Sent', 1)
    imap = ImapDeMentira(buzon, sin_leer={1, 2, 3, 5}, marcados={4})
    traidos = lectura.refrescar_banderas(
        imap, db, 'b@gmail.com', 'Todos',
        lambda m, uid: almacen.guardar(db, m, 'b@gmail.com', 'Todos', uid))
check('lo marcado que no se tenía se trae, y sólo eso', (traidos, imap.pedidos), (1, [4]))

desde = (HOY - timedelta(days=30)).isoformat()
pend = almacen.con_estado(desde, [CASA], ruta=BD2)
check('pendiente: ni boletines ni lo de la casa', sorted(m['asunto'] for m in pend),
      ['Pregunta', 'Revisar contrato', 'Viejo marcado'])
por = {m['asunto']: m for m in pend}
check('lo marcado, en qué cuenta', por['Viejo marcado']['marcado_en'], ['b@gmail.com'])
check('lo sin leer, en qué cuenta', por['Revisar contrato']['sin_leer_en'], ['b@gmail.com'])
check('se sabe contestado por la respuesta guardada',
      (por['Pregunta']['contestado'], por['Revisar contrato']['contestado']), (True, False))

check('lo automático se reconoce por la dirección',
      [almacen.automatico(d) for d in ('googleplay-noreply@google.com',
                                       'notifications@rambox.app',
                                       'recommendations@discover.pinterest.com',
                                       'updates@email.grok.com', 'ana@cuenca.gob.ec',
                                       'gescalera@cee.uned.es')],
      [True, True, True, True, False, False])
with almacen.abrir(BD2) as db:
    aviso = con_fecha(mensaje('Tu suscripción', 'x', de='Google <googleone-noreply@google.com>',
                              mid='<g1@x>'), reciente)
    almacen.guardar(db, lectura.leer(aviso), 'b@gmail.com', 'Todos', 6)
    almacen.guardar(db, lectura.leer(con_fecha(
        mensaje('Recibo', 'x', de='no-reply@stripe.com', mid='<g2@x>'), reciente)),
        'b@gmail.com', 'Todos', 7)
    imap = ImapDeMentira(buzon, sin_leer={1, 2, 3, 5, 6, 7}, marcados={4, 7})
    lectura.refrescar_banderas(imap, db, 'b@gmail.com', 'Todos', lambda m, uid: None)
pend = sorted(m['asunto'] for m in almacen.con_estado(desde, [CASA], ruta=BD2))
check('lo sin leer automático no cuenta; lo automático marcado, sí', pend,
      ['Pregunta', 'Recibo', 'Revisar contrato', 'Viejo marcado'])

with almacen.abrir(BD2) as db:
    imap = ImapDeMentira(buzon, sin_leer={2, 3}, marcados={4}, contestados={1})
    lectura.refrescar_banderas(imap, db, 'b@gmail.com', 'Todos', lambda m, uid: None)
pend = {m['asunto']: m for m in almacen.con_estado(desde, [CASA], ruta=BD2)}
check('lo leído en el teléfono deja de estar sin leer', sorted(pend), ['Viejo marcado'])

with almacen.abrir(BD2) as db:
    imap = ImapDeMentira(buzon, sin_leer={1}, marcados=set(), contestados={1})
    lectura.refrescar_banderas(imap, db, 'b@gmail.com', 'Todos', lambda m, uid: None)
filas = publicar.para_publicar(ruta=BD2, propias=[CASA])
check('lo desmarcado ya no sube; lo sin leer, con su estado',
      [(f['asunto'], sorted((v['tipo'], v['clave']) for v in f['vinculos'])) for f in filas],
      [('Revisar contrato', [('contestado', ''), ('sin_leer', 'b@gmail.com')])])

print()
if fallos:
    print(f'{len(fallos)} FALLA(N)')
    sys.exit(1)
print('Todo bien.')
