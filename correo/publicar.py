# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Lleva a la plataforma los correos relacionados. Sólo esos.

El servidor no puede leer el correo: las credenciales viven en esta
computadora. Lo que se le manda es lo que necesita para enseñarlo y para poner
los plazos en el cronograma —remitente, asunto, el principio del texto, en qué
cuentas está y con qué tiene que ver—, y sólo de los correos que tienen que ver
con algo: un quipux, una invitación, un plazo. El resto del buzón se queda aquí.

Entra por la aplicación, con el mismo secreto y la misma puerta que usa
`tools/publicar_quipux.py`: APP_URL y CRON_SECRET en el .env.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote

from . import almacen

# Lo que hace que un correo suba. Los avisos de la plataforma no: ya están en
# la plataforma, son ella.
TIPOS = ('quipux', 'cita', 'plazo')
LARGO_RESUMEN = 400


def huella(clave):
    """El id público del mensaje. El Message-ID no se usa tal cual: lleva
    ángulos, arrobas y a veces el nombre del equipo que lo escribió."""
    return hashlib.sha1(clave.encode('utf-8')).hexdigest()[:24]


def _enlace(clave, cuentas):
    """Para abrirlo en Gmail, si está en alguna cuenta de Gmail. Outlook no
    deja llegar a un mensaje por su Message-ID desde fuera."""
    gmail = [c for c in cuentas if c.endswith('@gmail.com')]
    if not gmail or clave.startswith('sin-id:'):
        return None
    return (f'https://mail.google.com/mail/u/{quote(gmail[0])}/'
            f'#search/rfc822msgid%3A{quote(clave, safe="")}')


def para_publicar(ruta=None):
    salida = {}
    for tipo in TIPOS:
        for m in almacen.vinculados(tipo, tope=5000, ruta=ruta):
            if m['id'] in salida:
                continue
            vinculos = [v for v in m['vinculos'] if v['tipo'] in TIPOS]
            texto = ' '.join((m.get('texto') or '').split())
            salida[m['id']] = {
                'id': huella(m['clave']),
                'fecha': m.get('fecha'),
                'de': (m.get('de') or '')[:300],
                'asunto': (m.get('asunto') or '')[:500],
                'resumen': texto[:LARGO_RESUMEN],
                'cuentas': ','.join(m['cuentas']),
                'n_adjuntos': len(m.get('adjuntos') or []),
                'enlace': _enlace(m['clave'], m['cuentas']),
                'vinculos': [{'tipo': v['tipo'], 'clave': v['clave'],
                              'detalle': v.get('detalle'), 'seguro': bool(v['seguro'])}
                             for v in vinculos],
            }
    return sorted(salida.values(), key=lambda x: x['fecha'] or '', reverse=True)


def mandar(mensajes):
    """Los deja en la plataforma. Devuelve la respuesta del servidor."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    destino = (os.getenv('APP_URL') or 'https://calendario.pensamiento-libre.org').rstrip('/')
    secreto = os.getenv('CRON_SECRET') or ''
    if not secreto:
        raise RuntimeError('Falta CRON_SECRET en el .env: sin él la plataforma no '
                           'acepta lo que se le manda.')
    cuerpo = json.dumps({'mensajes': mensajes}, ensure_ascii=False).encode('utf-8')
    pet = urllib.request.Request(
        destino + '/correo/api/publicar', data=cuerpo, method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                 'X-Cron-Secret': secreto,
                 # Cloudflare rechaza a quien no se presenta (ver publicar_quipux).
                 'User-Agent': 'calendarios-map/publicar-correo'})
    try:
        with urllib.request.urlopen(pet, timeout=180) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detalle = e.read().decode('utf-8', 'ignore')[:300]
        raise RuntimeError(f'el servidor contestó {e.code}: {detalle}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'no se pudo hablar con {destino}: {str(e)[:200]}')
