# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Por dónde se entra a cada buzón.

Las nueve cuentas ya están dadas de alta en el conector de correo que usa
Claude (`~/.claude/tools/gmail-mcp`): allí está la lista, la contraseña de
aplicación de cada Gmail en el llavero de Windows y el token de Microsoft en una
caché cifrada con la cuenta de Windows. Darlas de alta otra vez aquí sería tener
dos sitios donde renovar la misma clave, y el día que se renueve en uno el otro
deja de entrar sin que nadie sepa por qué.

Así que no se copia nada: se lee de allí, en el momento. Si la carpeta está en
otro sitio, `CORREO_CONECTOR` dice dónde.
"""
import imaplib
import json
import os
import re
import sys

# El nombre con el que el conector guarda las contraseñas en el llavero. Es
# suyo: si lo cambia allí, hay que cambiarlo aquí.
SERVICIO_LLAVERO = 'claude-gmail-mcp'

IMAP = {
    'gmail':     'imap.gmail.com',
    'microsoft': 'outlook.office365.com',
}


class ErrorCorreo(Exception):
    pass


def carpeta_del_conector():
    puesta = (os.getenv('CORREO_CONECTOR') or '').strip()
    if puesta:
        return puesta
    return os.path.join(os.path.expanduser('~'), '.claude', 'tools', 'gmail-mcp')


def cuentas():
    """Las cuentas dadas de alta, con su proveedor siempre puesto. Las de
    `cuentas_ews.json` —Exchange con el IMAP cerrado, ver `ews.py`— van al
    final, marcadas con `via: ews`."""
    ruta = os.path.join(carpeta_del_conector(), 'cuentas.json')
    if not os.path.exists(ruta):
        raise ErrorCorreo(
            f'No está la lista de cuentas ({ruta}). Se da de alta con el '
            'conector de correo; para otra carpeta, pon CORREO_CONECTOR.')
    with open(ruta, encoding='utf-8') as f:
        lista = json.load(f).get('cuentas', [])
    salida = [{**c, 'proveedor': c.get('proveedor') or 'gmail'} for c in lista]
    ruta_ews = os.path.join(carpeta_del_conector(), 'cuentas_ews.json')
    if os.path.exists(ruta_ews):
        with open(ruta_ews, encoding='utf-8') as f:
            salida += [{**c, 'proveedor': 'microsoft', 'via': 'ews'}
                       for c in json.load(f).get('cuentas', [])]
    return salida


def elegir(nombre=None):
    """Una cuenta por alias o dirección; todas si no se dice cuál."""
    todas = cuentas()
    if not nombre or nombre.lower() in ('todas', 'todos', '*'):
        return todas
    n = nombre.strip().lower()
    hallada = [c for c in todas if c['alias'].lower() == n or c['direccion'].lower() == n]
    if not hallada:
        raise ErrorCorreo(f"No hay ninguna cuenta «{nombre}». Están: "
                          + ', '.join(c['alias'] for c in todas))
    return hallada


def _oauth_ms():
    """El módulo del conector que renueva el token de Microsoft.

    Se importa de su carpeta y no se copia: la caché cifrada vive junto a él y
    sólo él sabe leerla."""
    donde = carpeta_del_conector()
    if donde not in sys.path:
        sys.path.insert(0, donde)
    import oauth_ms
    return oauth_ms


def como_renovar(cuenta):
    """Qué hacer cuando la cuenta deja de entrar, dicho para quien lo va a hacer."""
    carpeta = carpeta_del_conector()
    if cuenta.get('via') == 'ews':
        return ('Vuelve a autorizarla: en ' + carpeta + ' ejecuta  '
                f'.venv\\Scripts\\python.exe alta_microsoft.py {cuenta["direccion"]} x org  '
                'y sigue el código en microsoft.com/devicelogin. Al final dirá que IMAP '
                'falla: es lo esperado, esta cuenta se lee por EWS. Si aun así no '
                'entra, puede que Microsoft haya cerrado EWS.')
    if cuenta['proveedor'] == 'microsoft':
        tipo = 'personal' if 'consumers' in (cuenta.get('authority') or '') else 'org'
        return ('Vuelve a autorizarla: en ' + carpeta + ' ejecuta  '
                f'.venv\\Scripts\\python.exe alta_microsoft.py {cuenta["direccion"]} '
                f'{cuenta.get("alias") or ""} {tipo}  y sigue el código en '
                'microsoft.com/devicelogin.')
    return ('Crea una contraseña de aplicación nueva en '
            'myaccount.google.com/apppasswords (con esa cuenta abierta) y dala de alta: '
            'en ' + carpeta + ' ejecuta  .venv\\Scripts\\python.exe setup_cuenta.py')


def tiene_credencial(cuenta):
    try:
        if cuenta['proveedor'] == 'microsoft':
            return bool(_oauth_ms().tiene_token(cuenta['direccion']))
        import keyring
        return keyring.get_password(SERVICIO_LLAVERO, cuenta['direccion']) is not None
    except Exception:
        return False


def conectar(cuenta):
    """Una conexión IMAP abierta y autenticada. Lanza ErrorCorreo si no se puede."""
    direccion = cuenta['direccion']
    host = IMAP.get(cuenta['proveedor'], IMAP['gmail'])
    try:
        con = imaplib.IMAP4_SSL(host, 993, timeout=60)
    except Exception as e:
        raise ErrorCorreo(f'{direccion}: no contesta el servidor ({str(e)[:120]})')
    try:
        if cuenta['proveedor'] == 'microsoft':
            ms = _oauth_ms()
            token = ms.access_token(direccion, cuenta.get('authority'))
            cadena = ms.xoauth2(direccion, token).encode()
            con.authenticate('XOAUTH2', lambda _=None: cadena)
        else:
            import keyring
            secreto = keyring.get_password(SERVICIO_LLAVERO, direccion)
            if not secreto:
                raise ErrorCorreo(f'{direccion}: no hay contraseña en el llavero')
            con.login(direccion, secreto)
    except ErrorCorreo:
        raise
    except Exception as e:
        try:
            con.logout()
        except Exception:
            pass
        raise ErrorCorreo(f'{direccion}: no deja entrar ({str(e)[:150]})')
    return con


# ============================================================
#  QUÉ CARPETAS SE LEEN
# ============================================================
RE_LISTA = re.compile(r'\((?P<marcas>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<nombre>.+)$')


def _carpetas(con):
    """[(nombre tal como lo quiere SELECT, marcas en minúsculas)]"""
    estado, datos = con.list()
    if estado != 'OK':
        return []
    salida = []
    for linea in datos or []:
        if not linea:
            continue
        texto = linea.decode('utf-8', 'replace') if isinstance(linea, bytes) else str(linea)
        m = RE_LISTA.match(texto)
        if m:
            salida.append((m.group('nombre').strip(), m.group('marcas').lower()))
    return salida


def carpetas_a_leer(con, cuenta):
    """Lo que de verdad es correo de la persona: ni papelera, ni spam, ni borradores.

    En Gmail basta una carpeta: «Todos» lo tiene todo —recibido, enviado y
    archivado— y una etiqueta no es una copia. Leer también la bandeja de
    entrada y los enviados sería traer los mismos mensajes tres veces.

    En Microsoft las carpetas sí son sitios distintos, y lo que se archiva o se
    contesta deja de estar en la bandeja de entrada. Se leen las tres."""
    lista = _carpetas(con)
    if cuenta['proveedor'] == 'gmail':
        todo = [n for n, marcas in lista if '\\all' in marcas]
        return todo[:1] or ['INBOX']
    elegidas = ['INBOX']
    for marca in ('\\sent', '\\archive'):
        for n, marcas in lista:
            if marca in marcas and n not in elegidas:
                elegidas.append(n)
                break
    return elegidas


def entrecomillar(carpeta):
    """imaplib no entrecomilla: «Elementos enviados» sin comillas da BAD."""
    if carpeta.startswith('"'):
        return carpeta
    return '"%s"' % carpeta.replace('\\', '\\\\').replace('"', '\\"')
