# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los buzones de Exchange que tienen el IMAP cerrado, por EWS.

Hay organizaciones —la UNED con sus estudiantes— que tienen el buzón en
Exchange Online y el IMAP desactivado. Microsoft deja entrar («User is
authenticated») y luego no conecta el buzón. EWS, el servicio web de Exchange,
sí está abierto, con el mismo inicio de sesión de Microsoft que ya se guarda.

Estas cuentas no van en `cuentas.json` del conector de correo: el conector sólo
sabe IMAP, y con ellas en su lista fallaría cada vez que se le pidiera mirar
«todas». Van en `cuentas_ews.json`, en la misma carpeta y con la misma forma.

Lo que se trae es lo mismo que por IMAP y se guarda igual: el mensaje entero
(MIME) pasa por `lectura.leer`, y el estado —sin leer, marcado, contestado— va
a las mismas columnas. EWS no numera los mensajes como IMAP; el «uid» de cada
copia es una huella de su identificador, estable mientras no cambie de carpeta.

OJO: Microsoft está retirando EWS de Exchange Online. El día que lo cierre, la
cuenta saldrá en rojo en /correo como cualquier otra que no entre.
"""
import base64
import hashlib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import almacen, conector, lectura

URL = 'https://outlook.office365.com/EWS/Exchange.asmx'
ALCANCE = ['https://outlook.office365.com/EWS.AccessAsUser.All']
CARPETAS = ('inbox', 'sentitems')
PAGINA = 200
LOTE = 20

NS = {'s': 'http://schemas.xmlsoap.org/soap/envelope/',
      't': 'http://schemas.microsoft.com/exchange/services/2006/types',
      'm': 'http://schemas.microsoft.com/exchange/services/2006/messages'}

# Propiedades MAPI: estado de la bandera (2 = marcada) y el último verbo
# (102 responder, 103 responder a todos; 104 reenviar no es contestar).
BANDERA = '0x1090'
ULTIMO_VERBO = '0x1081'


def uid_de(item_id):
    """Un entero estable para la columna `uid` (cabe en el INTEGER de SQLite)."""
    return int(hashlib.sha1(item_id.encode()).hexdigest()[:15], 16)


# ============================================================
#  LA LLAMADA
# ============================================================
def _token(cuenta):
    ms = conector._oauth_ms()
    app = ms._app(cuenta['direccion'], cuenta.get('authority'))
    cuentas = app.get_accounts(username=cuenta['direccion'])
    r = app.acquire_token_silent(ALCANCE, account=cuentas[0]) if cuentas else None
    if not r or 'access_token' not in r:
        raise conector.ErrorCorreo(f"{cuenta['direccion']}: sin permiso de Microsoft "
                                   '(hay que volver a autorizarla)')
    return r['access_token']


def _sobre(cuerpo):
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types" '
            'xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">'
            '<soap:Header><t:RequestServerVersion Version="Exchange2016"/></soap:Header>'
            f'<soap:Body>{cuerpo}</soap:Body></soap:Envelope>')


def _llamar(token, direccion, cuerpo):
    pet = urllib.request.Request(URL, data=_sobre(cuerpo).encode('utf-8'), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'text/xml; charset=utf-8'})
    try:
        with urllib.request.urlopen(pet, timeout=90) as r:
            raiz = ET.fromstring(r.read())
    except urllib.error.HTTPError as e:
        raise conector.ErrorCorreo(f'{direccion}: EWS contestó {e.code} '
                                   f"({e.headers.get('x-ms-diagnostics') or ''})"[:200])
    except urllib.error.URLError as e:
        raise conector.ErrorCorreo(f'{direccion}: no contesta EWS ({str(e)[:120]})')
    for msg in raiz.iter():
        if msg.get('ResponseClass') == 'Error':
            texto = msg.findtext('m:MessageText', '', NS)
            raise conector.ErrorCorreo(f'{direccion}: EWS — {texto[:150]}')
    return raiz


# ============================================================
#  QUÉ HAY EN UNA CARPETA
# ============================================================
_FORMA = ('<m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties>'
          '<t:FieldURI FieldURI="item:DateTimeReceived"/>'
          '<t:FieldURI FieldURI="message:IsRead"/>'
          f'<t:ExtendedFieldURI PropertyTag="{BANDERA}" PropertyType="Integer"/>'
          f'<t:ExtendedFieldURI PropertyTag="{ULTIMO_VERBO}" PropertyType="Integer"/>'
          '</t:AdditionalProperties></m:ItemShape>')


def _restriccion_desde(desde_iso):
    return ('<m:Restriction><t:IsGreaterThanOrEqualTo>'
            '<t:FieldURI FieldURI="item:DateTimeReceived"/>'
            f'<t:FieldURIOrConstant><t:Constant Value="{desde_iso}"/></t:FieldURIOrConstant>'
            '</t:IsGreaterThanOrEqualTo></m:Restriction>')


_RESTRICCION_MARCADOS = (
    '<m:Restriction><t:IsEqualTo>'
    f'<t:ExtendedFieldURI PropertyTag="{BANDERA}" PropertyType="Integer"/>'
    '<t:FieldURIOrConstant><t:Constant Value="2"/></t:FieldURIOrConstant>'
    '</t:IsEqualTo></m:Restriction>')


def _extendida(item, etiqueta):
    for ext in item.findall('t:ExtendedProperty', NS):
        uri = ext.find('t:ExtendedFieldURI', NS)
        if uri is not None and (uri.get('PropertyTag') or '').lower() == etiqueta:
            try:
                return int(ext.findtext('t:Value', '', NS))
            except ValueError:
                return None
    return None


def _buscar(token, direccion, carpeta, restriccion, tope):
    """[{id, sin_leer, marcado, contestado}] de la carpeta, por páginas."""
    salida, desde = [], 0
    while len(salida) < tope:
        raiz = _llamar(token, direccion, (
            '<m:FindItem Traversal="Shallow">' + _FORMA +
            f'<m:IndexedPageItemView MaxEntriesReturned="{PAGINA}" Offset="{desde}" '
            'BasePoint="Beginning"/>' + restriccion +
            f'<m:ParentFolderIds><t:DistinguishedFolderId Id="{carpeta}"/></m:ParentFolderIds>'
            '</m:FindItem>'))
        items = list(raiz.iter('{%s}Message' % NS['t']))
        for it in items:
            ident = it.find('t:ItemId', NS)
            if ident is None:
                continue
            salida.append({
                'id': ident.get('Id'),
                'sin_leer': it.findtext('t:IsRead', 'true', NS) == 'false',
                'marcado': _extendida(it, BANDERA) == 2,
                'contestado': _extendida(it, ULTIMO_VERBO) in (102, 103),
            })
        vista = next(raiz.iter('{%s}RootFolder' % NS['m']), None)
        if not items or vista is None or vista.get('IncludesLastItemInRange') == 'true':
            break
        desde += len(items)
    return salida[:tope]


def _mime(token, direccion, ids):
    """{id: bytes} del mensaje entero, para pasarlo por `lectura.leer`."""
    raiz = _llamar(token, direccion, (
        '<m:GetItem><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape>'
        '<t:IncludeMimeContent>true</t:IncludeMimeContent></m:ItemShape><m:ItemIds>'
        + ''.join(f'<t:ItemId Id="{i}"/>' for i in ids) +
        '</m:ItemIds></m:GetItem>'))
    salida = {}
    for it in raiz.iter('{%s}Message' % NS['t']):
        ident, mime = it.find('t:ItemId', NS), it.findtext('t:MimeContent', '', NS)
        if ident is not None and mime:
            salida[ident.get('Id')] = base64.b64decode(mime)
    return salida


# ============================================================
#  UNA CUENTA
# ============================================================
def leer_cuenta(cuenta, db, alguardar, dias=lectura.DIAS_PRIMERA_VEZ):
    """Lo de los últimos `dias` y todo lo marcado, de la bandeja y de los
    enviados; y el estado de cada uno. `alguardar(mensaje, uid, carpeta)`.
    Devuelve {'leidos', 'marcados_viejos', 'carpetas'}."""
    direccion = cuenta['direccion']
    token = _token(cuenta)
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime('%Y-%m-%dT%H:%M:%SZ')
    res = {'leidos': 0, 'marcados_viejos': 0, 'carpetas': list(CARPETAS)}
    for carpeta in CARPETAS:
        recientes = _buscar(token, direccion, carpeta, _restriccion_desde(desde),
                            lectura.TOPE_POR_CARPETA)
        marcados = _buscar(token, direccion, carpeta, _RESTRICCION_MARCADOS,
                           lectura.TOPE_MARCADOS)
        todos = {i['id']: i for i in recientes + marcados}
        ya = almacen.uids_de(db, direccion, carpeta)
        faltan = [i for i in todos.values() if uid_de(i['id']) not in ya]
        recientes_ids = {i['id'] for i in recientes}
        for n in range(0, len(faltan), LOTE):
            lote = faltan[n:n + LOTE]
            for ident, crudo in _mime(token, direccion, [i['id'] for i in lote]).items():
                try:
                    alguardar(lectura.leer(crudo, todos[ident]['sin_leer']),
                              uid_de(ident), carpeta)
                    if ident in recientes_ids:
                        res['leidos'] += 1
                    else:
                        res['marcados_viejos'] += 1
                except Exception as e:
                    print(f'[correo] {direccion} EWS: no se pudo leer ({str(e)[:100]})')
            db.commit()
        almacen.apuntar_banderas(
            db, direccion, carpeta,
            {uid_de(i['id']) for i in todos.values() if i['sin_leer']},
            {uid_de(i['id']) for i in todos.values() if i['marcado']},
            {uid_de(i['id']) for i in todos.values() if i['contestado']})
        db.commit()
    return res
