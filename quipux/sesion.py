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
        # La página que dejó el salto posterior a la credencial, cuando ese
        # salto ya se dio para comprobar si había control de imagen.
        self._pagina_tras_salto = None

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
    #  Las redirecciones que no son redirecciones
    # ------------------------------------------------------------------
    # CuencaDOC no redirige con una cabecera HTTP: contesta 200 con una línea
    # de JavaScript —`window.location='…'` o `top.window.location='…'`— y
    # espera que el navegador la obedezca. Un cliente HTTP no obedece nada: ve
    # un 200 con setenta y nueve bytes dentro y da la petición por buena.
    #
    # Ahí se rompía el acceso. El POST de la credencial ERA correcto, y el
    # sistema contestaba «pasa por aquí»; como nadie iba, la sesión se quedaba
    # a medio hacer y la página siguiente respondía «no te conozco». El fallo
    # se veía como una credencial rechazada, que es lo que más despista: la
    # credencial estaba bien.
    RE_SALTO = re.compile(
        r"""(?:top\.)?(?:window\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""", re.I)

    def _seguir_saltos(self, r, tope=5):
        """Obedece las redirecciones por JavaScript. Devuelve la última página."""
        for _ in range(tope):
            if not r.text or len(r.text) > 4000 or '<script' not in r.text.lower():
                return r
            m = self.RE_SALTO.search(r.text)
            if not m:
                return r
            destino = m.group(1).strip()
            if 'paginaerror' in destino.lower():
                return r          # es un rechazo; quien llamó decide qué decir
            r = self.get(destino if destino.startswith('http')
                         else destino.lstrip('./'))
        return r

    @staticmethod
    def _rechazado(r):
        return 'paginaerror' in (r.text or '').lower()

    def _pide_imagen(self, r):
        """¿Hace falta que una persona escriba el texto de una imagen?

        No basta con ver a dónde salta el sistema: tras la credencial SIEMPRE
        manda a `login_validar_captcha.php`, haya control o no. Fiarse de ese
        nombre hacía creer que el control estaba puesto cuando no lo estaba, y
        entonces se le pedía a alguien que leyera una imagen que no existía —o
        se daba por fallido un acceso que había ido bien.

        Lo que decide es lo que esa página TRAE: si tiene un campo `txt_captcha`
        que rellenar, hay control; si no, era sólo un paso intermedio."""
        texto = r.text or ''
        if 'txt_captcha' in texto:
            return True
        if 'login_validar_captcha' not in texto.lower():
            return False
        # Saltó ahí: hay que ir a ver si de verdad pide algo. Se sigue la
        # dirección TAL COMO viene, con la cola de parámetros que trae —el
        # usuario y la contraseña cifrada—: pedida a pelo, sin ellos, la página
        # no monta el control y contesta como si no hiciera falta. Ese es el
        # camino por el que un acceso que SÍ pedía la imagen acababa dándose
        # por credencial rechazada, con un mensaje que además culpaba a la
        # contraseña —el aviso que la propia página de acceso lleva escrito en
        # su JavaScript— cuando la contraseña estaba bien.
        m = self.RE_SALTO.search(texto)
        destino = (m.group(1).strip() if m else 'login_validar_captcha.php')
        try:
            pagina = self.get(destino if destino.startswith('http')
                              else destino.lstrip('./'))
        except Exception:
            return False
        # Ese salto sólo se puede dar UNA vez: la dirección lleva la credencial
        # y el sistema la da por gastada en cuanto se pide. Cuando no hay
        # control, esta misma petición es la que termina de abrir la sesión, así
        # que se guarda para que quien llamó siga desde aquí. Sin esto se
        # repetía el salto con la credencial ya gastada, y el sistema contestaba
        # que no conocía la sesión — justo después de haberla abierto.
        self._pagina_tras_salto = pagina
        return 'txt_captcha' in (pagina.text or '')

    # ------------------------------------------------------------------
    #  Entrar en dos tiempos, para que el captcha se resuelva desde la web
    # ------------------------------------------------------------------
    #
    # En el servidor no hay ninguna pantalla que abrir ni nadie sentado
    # delante. Pero el control de CuencaDOC sólo pide una cosa —el texto de una
    # imagen— y esa imagen se puede enseñar en la propia plataforma, que sí
    # tiene a alguien mirándola.
    #
    # Así que el acceso se parte en dos: `empezar()` hace lo que no necesita a
    # nadie y, si aparece el control, devuelve la imagen; `terminar(texto)`
    # cierra el acceso con lo que la persona escribió. Entre los dos momentos
    # la sesión HTTP se queda esperando, con sus cookies, que es justo lo que
    # hace falta: el captcha va atado a esa sesión y no a otra.
    def empezar(self):
        """Primer tiempo. Devuelve 'dentro' o ('captcha', imagen_png)."""
        usuario, contrasenia = credenciales.leer(self._usuario_pedido)
        self.usuario = usuario

        pagina = self.get('login.php')
        if pagina.status_code != 200:
            raise ErrorQuipux(f'La página de acceso no respondió (HTTP {pagina.status_code}). '
                              'Puede ser un corte del servicio del Municipio.')
        clave = re.search(r'public_key\s*=\s*[\'"]([^\'"]+)', pagina.text)
        token = re.search(r'token_login\s*=\s*[\'"]([^\'"]+)', pagina.text)
        if not clave or not token:
            raise ErrorQuipux('La página de acceso cambió: ya no trae la clave '
                              'pública ni el token. Hay que revisar este módulo.')
        import base64
        try:
            publica = load_pem_public_key(self._pem(clave.group(1)).encode())
            cifrado = publica.encrypt(
                f'{token.group(1)}|{contrasenia}'.encode(), padding.PKCS1v15())
        except Exception as e:
            raise ErrorQuipux(f'No se pudo cifrar la contraseña: {str(e)[:150]}')

        respuesta = self.post('login.php?txt_administrador=0', {
            'krd': usuario, 'drd': '',
            'txt_contrasenia': base64.b64encode(cifrado).decode(),
            'Submit': 'Ingresar'})

        if self._pide_imagen(respuesta):
            imagen = self.get('js/captcha.php')
            if imagen.status_code != 200 or not imagen.content:
                raise ErrorQuipux('CuencaDOC pide el texto de una imagen pero no '
                                  'entrega la imagen. Inténtalo de nuevo en un rato.')
            return 'captcha', imagen.content

        respuesta = self._seguir_saltos(self._pagina_tras_salto or respuesta)
        self._pagina_tras_salto = None
        if self._rechazado(respuesta):
            raise ErrorQuipux(
                f'CuencaDOC rechazó la credencial de «{usuario}». Revisa el usuario '
                'y la clave, y que la cuenta no esté bloqueada por intentos fallidos.')
        self.nombre = self._quien_soy()
        if not self.nombre:
            raise ErrorQuipux('Se envió la credencial pero el sistema no reconoce la sesión.')
        return 'dentro', None

    def terminar(self, texto_imagen):
        """Segundo tiempo: se manda lo que la persona leyó en la imagen.

        A dónde va esto no es evidente y cuesta un fallo averiguarlo: la página
        del control NO se envía a sí misma. Su `validar_login()` cambia el
        destino del formulario a `login.php?txt_administrador=0` —el mismo del
        primer paso— y manda `krd` y `txt_contrasenia` VACÍOS, porque el
        servidor ya guardó el usuario y la contraseña en la sesión al recibirlos
        antes. Lo único que aporta este envío es el texto de la imagen.

        Mandarlo a `login_validar_captcha.php`, que es lo que parece, hace que
        el sistema conteste que el texto no era correcto — aunque lo fuera."""
        pagina = self.get('login_validar_captcha.php')
        doc = lxml_html.fromstring(pagina.text)
        datos = {}
        for campo in doc.xpath('//input[@name]'):
            nombre = campo.get('name')
            if nombre and nombre.lower() != 'submit':
                datos[nombre] = campo.get('value') or ''
        datos['txt_captcha'] = (texto_imagen or '').strip()
        datos['Submit'] = 'Ingresar'

        respuesta = self._seguir_saltos(
            self.post('login.php?txt_administrador=0', datos))
        if self._rechazado(respuesta) or self._pide_imagen(respuesta):
            raise ErrorQuipux('El texto de la imagen no era correcto, o caducó. '
                              'Vuelve a intentarlo: se pedirá una imagen nueva.')
        self.nombre = self._quien_soy()
        if not self.nombre:
            raise ErrorQuipux('Se pasó el control pero el sistema no reconoce la sesión.')
        return self.nombre

    def galletas(self):
        """La sesión, para poder guardarla entre una petición web y la siguiente."""
        return {c.name: c.value for c in self.s.cookies}

    def poner_galletas(self, galletas, dominio='dq.cuenca.gob.ec'):
        for nombre, valor in (galletas or {}).items():
            self.s.cookies.set(nombre, valor, domain=dominio, path='/')

    def sigue_dentro(self):
        """¿La sesión guardada todavía vale? Quipux echa la anterior cuando se
        entra desde otro equipo, así que esto no es una formalidad."""
        try:
            return bool(self._quien_soy())
        except Exception:
            return False

    def _entrar_con_ayuda(self, usuario, contrasenia):
        """Cede el turno a la persona para el texto de la imagen, y recoge la
        sesión ya iniciada para seguir por HTTP."""
        from . import navegador
        cookies = navegador.entrar_asistido(self.base, usuario, contrasenia,
                                            registro=self.log)
        puestas = navegador.pasar_cookies(self.s, cookies)
        if not puestas:
            raise ErrorQuipux('Se completó el acceso pero no se pudo recoger la sesión.')
        self.log(f'[quipux] sesión recogida del navegador ({puestas} cookies)')

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

        # El sistema contesta «pasa por aquí» en JavaScript. Hay que ir: es en
        # esa segunda petición donde termina de montarse la sesión.
        if self._pide_imagen(respuesta):
            # El control de la imagen. No se lee ni se rodea: lo resuelve la
            # persona, que es de quien es la cuenta. Sale unas veces sí y otras
            # no, así que esto no puede ser el camino normal — sólo el desvío.
            self._entrar_con_ayuda(usuario, contrasenia)
        else:
            respuesta = self._seguir_saltos(self._pagina_tras_salto or respuesta)
            if self._rechazado(respuesta):
                raise ErrorQuipux(
                    f'CuencaDOC rechazó la credencial de «{usuario}». '
                    'Comprueba usuario y clave con «python -m quipux alta», y que la '
                    'cuenta no esté bloqueada por intentos fallidos.')
        self._pagina_tras_salto = None

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
        # La cabecera vive dentro del marco, y algunos Quipux sólo la sirven
        # después de haber pedido la página que la contiene.
        self._seguir_saltos(self.get('index_frames.php'))
        r = self._seguir_saltos(self.get('f_top.php'))
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
