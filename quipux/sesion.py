# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Entrar a CuencaDOC y moverse por dentro, sin navegador.

CuencaDOC es un Quipux: PHP con marcos, sesión de servidor y tablas armadas a
mano. Se podría manejar con un navegador teledirigido, pero para leer bandejas
y bajar adjuntos eso es cargar un camión para mover una caja: arranca Chrome,
espera pinturas de pantalla y se rompe en cuanto cambian un margen. Con
peticiones HTTP directas la misma pasada tarda segundos y sólo depende de que
el HTML siga diciendo lo que dice.

Cómo se entra (esto no está documentado en ninguna parte; sale de leer el
`validar_login()` de su propia página de acceso):

    1. GET /login.php trae, incrustados en el HTML, una clave pública RSA de
       1024 bits y un `token_login` de un solo uso.
    2. Se cifra la cadena  «token_login | contraseña»  con esa clave (RSA
       PKCS#1 v1.5, que es lo que hace JSEncrypt) y se manda en base64.
    3. El POST va a /login.php?txt_administrador=0 con el usuario en `krd`, el
       cifrado en `txt_contrasenia` y `drd` VACÍO — la página borra ese campo
       antes de enviar, y mandarlo con la contraseña dentro sería enviarla en
       claro por encima del cifrado que se acaba de hacer.

El token es de un solo uso: cada intento necesita su propio GET previo. Por eso
no se cachea nada aquí.

Lo demás —qué bandejas hay, con qué parámetros se piden, dónde está el
desplegable de áreas— NO se escribe a mano en este archivo: se descubre leyendo
el menú y la cabecera en cada sesión. Un Quipux actualizado cambia esas rutas
antes que su estructura, y una lista escrita a mano aquí envejece en silencio:
seguiría corriendo, y devolviendo cero documentos como si no hubiera trabajo.
"""
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from lxml import html as lxml_html

from . import credenciales

BASE = 'https://dq.cuenca.gob.ec/'
NAVEGADOR = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# Entre peticiones. No es por cortesía: es un servidor público del municipio y
# una ráfaga de cientos de descargas seguidas se parece demasiado a un ataque.
PAUSA = 0.4


class ErrorQuipux(Exception):
    """Algo salió mal con CuencaDOC. El texto va dirigido a quien lo va a leer
    a las siete de la mañana, no al que escribió el código."""


class Quipux:
    def __init__(self, usuario=None, base=BASE, pausa=PAUSA, registro=print):
        self.base = base
        self.pausa = pausa
        self.log = registro or (lambda *a, **k: None)
        self._usuario_pedido = usuario
        self.usuario = None
        self.nombre = ''
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': NAVEGADOR})
        self._perfiles = None

    # ------------------------------------------------------------------
    #  Peticiones
    # ------------------------------------------------------------------
    def _url(self, ruta):
        return urljoin(self.base, ruta)

    def get(self, ruta, **kw):
        time.sleep(self.pausa)
        kw.setdefault('timeout', 40)
        return self.s.get(self._url(ruta), **kw)

    def post(self, ruta, datos, **kw):
        time.sleep(self.pausa)
        kw.setdefault('timeout', 40)
        return self.s.post(self._url(ruta), data=datos, **kw)

    # ------------------------------------------------------------------
    #  Entrar
    # ------------------------------------------------------------------
    @staticmethod
    def _pem(crudo):
        """La clave viene en una sola línea, sin los saltos que exige el PEM."""
        v = (crudo or '').replace('\\n', '\n').replace('\\/', '/').strip()
        if 'BEGIN' in v and '\n' in v:
            return v
        cuerpo = re.sub(r'-----[^-]+-----', '', v).replace('\n', '').strip()
        troceada = '\n'.join(cuerpo[i:i + 64] for i in range(0, len(cuerpo), 64))
        return f'-----BEGIN PUBLIC KEY-----\n{troceada}\n-----END PUBLIC KEY-----\n'

    def entrar(self):
        """Inicia sesión. Devuelve el nombre del usuario tal como lo saluda el
        sistema, que es la única confirmación fiable de que se entró."""
        usuario, contrasenia = credenciales.leer(self._usuario_pedido)
        self.usuario = usuario

        pagina = self.get('login.php')
        if pagina.status_code != 200:
            raise ErrorQuipux(f'La página de acceso no respondió (HTTP {pagina.status_code}). '
                              'Puede ser un corte del servicio del Municipio.')

        clave = re.search(r'public_key\s*=\s*[\'"]([^\'"]+)', pagina.text)
        token = re.search(r'token_login\s*=\s*[\'"]([^\'"]+)', pagina.text)
        if not clave or not token:
            raise ErrorQuipux(
                'La página de acceso ya no trae la clave pública o el token. '
                'CuencaDOC cambió su forma de entrar y hay que revisar este módulo.')

        try:
            publica = load_pem_public_key(self._pem(clave.group(1)).encode())
            cifrado = publica.encrypt(
                f'{token.group(1)}|{contrasenia}'.encode(), padding.PKCS1v15())
        except Exception as e:
            raise ErrorQuipux(f'No se pudo cifrar la contraseña: {str(e)[:150]}')

        import base64
        respuesta = self.post('login.php?txt_administrador=0', {
            'krd': usuario,
            'drd': '',                                  # la página lo vacía antes de enviar
            'txt_contrasenia': base64.b64encode(cifrado).decode(),
            'Submit': 'Ingresar',
        }, allow_redirects=True)

        if respuesta.status_code != 200:
            raise ErrorQuipux(f'El acceso respondió HTTP {respuesta.status_code}.')

        texto = respuesta.text
        # Que la respuesta sea 200 no significa que se entró: un usuario o una
        # clave mal puestos devuelven la misma página de acceso, con el aviso
        # dentro. Confundir las dos cosas haría que la pasada siguiera adelante
        # y acabara informando de que no hay ningún documento.
        if 'form_login' in texto and 'public_key' in texto:
            motivo = self._motivo_del_rechazo(texto)
            raise ErrorQuipux(f'CuencaDOC no aceptó la credencial de «{usuario}». {motivo}')

        self.nombre = self._quien_soy()
        if not self.nombre:
            raise ErrorQuipux('Se envió la credencial pero el sistema no reconoce la sesión.')
        self.log(f'[quipux] dentro como {self.nombre}')
        return self.nombre

    @staticmethod
    def _motivo_del_rechazo(texto):
        for patron in (r'alert\s*\(\s*[\'"]([^\'"]{10,200})',
                       r'class="[^"]*error[^"]*"[^>]*>\s*([^<]{10,200})'):
            m = re.search(patron, texto, re.I)
            if m:
                return re.sub(r'\s+', ' ', m.group(1)).strip()
        return ('Revisa el usuario y la clave con «python -m quipux alta», y '
                'comprueba que la cuenta no esté bloqueada por intentos fallidos.')

    def _quien_soy(self):
        """El nombre y el área salen de la cabecera. Sirve de comprobante."""
        r = self.get('f_top.php')
        if r.status_code != 200:
            return ''
        doc = lxml_html.fromstring(r.text)
        for opcion in doc.xpath('//select[@name="cargo_usuario"]/option'):
            if opcion.get('selected') is not None:
                return re.sub(r'\s+', ' ', opcion.text_content()).strip()
        texto = doc.text_content()
        m = re.search(r'Usuario:\s*(.{5,120})', texto)
        return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''

    def salir(self):
        """Cerrar la sesión al terminar. Una sesión abandonada en un sistema
        del municipio es una puerta que se queda entornada."""
        for ruta in ('logout.php', 'salir.php', 'index.php?logout=1'):
            try:
                if self.get(ruta).status_code == 200:
                    break
            except Exception:
                continue

    # ------------------------------------------------------------------
    #  Las áreas de la persona
    # ------------------------------------------------------------------
    def perfiles(self):
        """Las áreas entre las que puede alternar: Observatorio, Planificación…

        Cada una tiene SU bandeja: un documento dirigido al Observatorio no
        aparece estando en Planificación. Recorrer sólo la que sale por defecto
        es dejarse fuera media agenda sin que nada lo advierta."""
        if self._perfiles is not None:
            return self._perfiles
        r = self.get('f_top.php')
        doc = lxml_html.fromstring(r.text)
        salida = []
        for opcion in doc.xpath('//select[@name="cargo_usuario"]/option'):
            valor = (opcion.get('value') or '').strip()
            texto = re.sub(r'\s+', ' ', opcion.text_content()).strip()
            if not valor or not texto:
                continue
            area = ''
            m = re.search(r'Área:\s*([^/]+)', texto)
            if m:
                area = m.group(1).strip()
            salida.append({
                'id': valor,
                'texto': texto,
                'area': area or texto,
                'activo': opcion.get('selected') is not None,
                # Un perfil de ciudadano no tiene bandeja institucional: se
                # anota y se salta, en vez de recorrerlo para no encontrar nada.
                'institucional': bool(area),
            })
        self._perfiles = salida
        return salida

    def cambiar_perfil(self, id_cargo):
        """Se pone en el área indicada. Devuelve True si el sistema lo confirma.

        La ruta no se escribe a mano: se saca del `onchange` del propio
        desplegable, que es quien sabe a dónde manda el sistema cuando una
        persona lo usa. Si el Municipio actualiza el Quipux y esa ruta cambia,
        esto sigue funcionando."""
        r = self.get('f_top.php')
        doc = lxml_html.fromstring(r.text)
        sel = doc.xpath('//select[@name="cargo_usuario"]')
        destino = None
        if sel:
            onchange = sel[0].get('onchange') or ''
            m = re.search(r'[\'"]([\w./_-]+\.php)[^\'"]*[\'"]', onchange)
            if m:
                destino = m.group(1)
        # Rutas conocidas de Quipux, por si el desplegable no lo dice.
        candidatas = [d for d in (destino,
                                  'cambiar_usuario.php',
                                  'Administracion/usuarios/cambiar_usuario.php',
                                  'f_top.php') if d]
        for ruta in candidatas:
            for campo in ('cargo_usuario', 'id_cargo', 'usua_cargo'):
                try:
                    self.get(f'{ruta}?{campo}={id_cargo}')
                except Exception:
                    continue
                if self._perfil_activo() == str(id_cargo):
                    self._perfiles = None
                    return True
        return False

    def _perfil_activo(self):
        r = self.get('f_top.php')
        doc = lxml_html.fromstring(r.text)
        for o in doc.xpath('//select[@name="cargo_usuario"]/option'):
            if o.get('selected') is not None:
                return (o.get('value') or '').strip()
        return ''

    # ------------------------------------------------------------------
    #  Las bandejas
    # ------------------------------------------------------------------
    def bandejas(self):
        """Las bandejas del área en la que se está, tal como las lista el menú.

        Se leen del menú en vez de escribirlas aquí porque el nombre y el
        parámetro de cada una son cosa del sistema, no nuestra; y porque el
        número que trae entre paréntesis —«Recibidos (28)»— dice cuántos
        documentos debería haber, que es la única forma de saber después si la
        pasada se dejó algo."""
        r = self.get('menu/menu.php')
        if r.status_code != 200:
            raise ErrorQuipux(f'No se pudo leer el menú (HTTP {r.status_code}).')
        doc = lxml_html.fromstring(r.text)
        salida, vistas = [], set()
        for a in doc.xpath('//a[@href]'):
            href = (a.get('href') or '').strip()
            if 'cuerpo.php' not in href and 'bandeja' not in href.lower():
                continue
            texto = re.sub(r'\s+', ' ', a.text_content()).strip()
            if not texto:
                continue
            m = re.search(r'\((\d+)\s*(?:/\s*\d+)?\)', texto)
            nombre = re.sub(r'\s*\(.*?\)\s*$', '', texto).strip()
            ruta = urljoin('menu/', href)
            clave = (nombre, ruta)
            if clave in vistas:
                continue
            vistas.add(clave)
            salida.append({
                'nombre': nombre,
                'ruta': ruta,
                'esperados': int(m.group(1)) if m else None,
                'codigo': self._codigo_de(ruta),
            })
        return salida

    @staticmethod
    def _codigo_de(ruta):
        try:
            q = parse_qs(urlparse(ruta).query)
        except Exception:
            return ''
        for clave in ('bandeja', 'tipo_bandeja', 'ban'):
            if clave in q:
                return q[clave][0]
        return ''
