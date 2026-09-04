# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Cuando CuencaDOC pide el texto de la imagen, lo teclea una persona.

El acceso de CuencaDOC levanta a veces un control —«Por favor ingrese el texto
que se muestra en la imagen»— que existe justamente para que un programa no
entre solo. Ese control se respeta: no se lee la imagen, no se rodea y no se
reintenta hasta acertar. Lo resuelve quien tiene derecho a resolverlo, que es
el dueño de la cuenta, y tarda cinco segundos.

Lo que sí se puede quitar de en medio es todo lo demás. Este módulo abre el
navegador con el usuario y la clave ya puestos —los saca del llavero de
Windows, como haría cualquier gestor de contraseñas—, espera a que la persona
complete el acceso, y en cuanto está dentro se lleva la sesión ya iniciada al
cliente HTTP, que es quien hace el trabajo largo: dos áreas, siete bandejas,
doscientos y pico documentos y sus adjuntos.

Es decir: el navegador sólo para la puerta. Recorrer las bandejas con un
navegador teledirigido tardaría veinte veces más y se rompería en cuanto
cambiaran un margen de la página.

Y sólo se abre cuando hace falta. La mayoría de los días el control no aparece
y se entra directamente por HTTP, sin que nada se asome a la pantalla.
"""
import time

URL_LOGIN = 'login.php'
SENAL_DENTRO = ('index_frames.php', 'cuerpo.php')
ESPERA_MAXIMA = 300          # cinco minutos: da tiempo a ir por un café


class SinNavegador(Exception):
    """Playwright no está instalado o no encuentra un Chrome que abrir."""


def _abrir(p, registro):
    """Prefiere el Chrome que la persona ya tiene: mismo aspecto de siempre y
    ningún navegador extra que descargar. Si no está, usa el de Playwright."""
    for intento in ({'channel': 'chrome'}, {'channel': 'msedge'}, {}):
        try:
            return p.chromium.launch(headless=False, args=['--start-maximized'],
                                     **intento)
        except Exception:
            continue
    raise SinNavegador(
        'No se encontró ningún navegador que abrir. Instala uno con:\n'
        '    .\\venv\\Scripts\\python.exe -m playwright install chromium')


def entrar_asistido(base, usuario, contrasenia, registro=print,
                    espera_maxima=ESPERA_MAXIMA):
    """Abre el acceso, deja lo que se puede dejar hecho, y espera a la persona.

    Devuelve las cookies de la sesión ya iniciada, listas para el cliente HTTP.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SinNavegador(
            'Falta Playwright. Instálalo con:\n'
            '    .\\venv\\Scripts\\python.exe -m pip install playwright')

    registro('')
    registro('  ┌────────────────────────────────────────────────────────┐')
    registro('  │  CuencaDOC pide el texto de la imagen.                 │')
    registro('  │  Se abre el navegador con tu usuario y clave puestos:  │')
    registro('  │  escribe sólo el texto de la imagen y pulsa Ingresar.  │')
    registro('  └────────────────────────────────────────────────────────┘')
    registro('')

    with sync_playwright() as p:
        navegador = _abrir(p, registro)
        contexto = navegador.new_context(no_viewport=True)
        pagina = contexto.new_page()
        try:
            pagina.goto(base.rstrip('/') + '/' + URL_LOGIN, timeout=60000)

            # El usuario y la clave se rellenan desde el llavero. El texto de la
            # imagen NO: ese es el punto entero del control.
            for selector, valor in (('#krd', usuario), ('#drd', contrasenia)):
                try:
                    pagina.fill(selector, valor, timeout=8000)
                except Exception:
                    registro(f'  (no se pudo precargar {selector}; tecléalo a mano)')
            try:
                pagina.focus('input[name*="captcha" i], input[type="text"]:not(#krd)')
            except Exception:
                pass

            limite = time.time() + espera_maxima
            aviso = 0
            while time.time() < limite:
                url = (pagina.url or '').lower()
                if any(s in url for s in SENAL_DENTRO):
                    break
                try:
                    if pagina.locator('select[name="cargo_usuario"]').count():
                        break
                except Exception:
                    pass
                restante = int(limite - time.time())
                if restante % 30 == 0 and restante != aviso:
                    aviso = restante
                    registro(f'  esperando el acceso… ({restante} s)')
                time.sleep(1)
            else:
                raise SinNavegador(
                    'Se agotó la espera sin que se completara el acceso. '
                    'Vuelve a lanzarlo cuando puedas estar delante.')

            cookies = contexto.cookies()
            registro(f'  acceso completado; sigo yo desde aquí.')
            return cookies
        finally:
            try:
                contexto.close(); navegador.close()
            except Exception:
                pass


def pasar_cookies(sesion_requests, cookies, dominio_por_defecto='dq.cuenca.gob.ec'):
    """Traslada la sesión del navegador al cliente HTTP.

    Es el momento en que el trabajo cambia de manos: la persona ya abrió la
    puerta y a partir de aquí no hace falta pantalla ninguna."""
    puestas = 0
    for c in cookies or []:
        nombre, valor = c.get('name'), c.get('value')
        if not nombre or valor is None:
            continue
        dominio = (c.get('domain') or dominio_por_defecto).lstrip('.')
        if 'cuenca.gob.ec' not in dominio:
            continue                    # nada de cookies de terceros
        sesion_requests.cookies.set(nombre, valor, domain=dominio,
                                    path=c.get('path') or '/')
        puestas += 1
    return puestas
