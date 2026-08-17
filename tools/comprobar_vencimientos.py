#!/usr/bin/env python3
# =============================================================================
# comprobar_vencimientos.py
# Desarrollado por Marco Antonio Posligua San Martín
#
# Dice si el aviso de incumplimiento está de verdad en pie, y si no lo está,
# cuál de las tres piezas falta.
#
# Por qué existe: el calendario de vencimientos depende de dos cosas que NO
# viajan en el repositorio —una tabla que hay que crear en Supabase y unas
# claves de correo que hay que poner en el entorno—. Sin ellas la aplicación
# arranca igual, la pantalla funciona igual y el proyecto se guarda igual; lo
# único que no pasa es que llegue el correo el día que algo se incumple. Es
# decir: falla exactamente el día en que hacía falta, y en silencio.
#
# Esto lo saca a la luz antes. Comprueba, por orden:
#   1. La tabla `vencimiento_avisos` (migración 030). Sin ella no hay memoria
#      de qué se avisó y el aviso se puede repetir o perderse.
#      (La 031 es aparte: añade `completed_date` al cronograma, que es lo que
#       separa «cumplida» de «cumplida con retraso». No la mira esto porque el
#       correo no depende de ella.)
#   2. Que la consulta de vencimientos responda rápido. Si tarda, es que los
#      índices de la 030 no se aplicaron: en frío PostgreSQL cancela la
#      consulta y el sistema recibe una lista vacía, o sea «no se incumple
#      nada», que es la peor respuesta posible porque parece buena.
#   3. El servidor de correo: que esté configurado y que acepte la clave.
#      Con Gmail hace falta una contraseña de aplicación, no la de la cuenta.
#
# Uso:
#   python tools/comprobar_vencimientos.py            # sólo comprueba
#   python tools/comprobar_vencimientos.py --enviar   # además manda un correo
#                                                     # de prueba al destino
#
# Sale con 1 si algo falta, para poder encadenarlo en un despliegue.
# =============================================================================
import os
import smtplib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv                              # noqa: E402
load_dotenv()

from app import SupabaseAPI                                 # noqa: E402
from app import avisos                                      # noqa: E402
from config.config import Config                            # noqa: E402


OK, MAL, AVISO = '  [OK]  ', '  [FALTA]', '  [OJO] '


class AppMinima:
    """Lo justo que `avisos.py` necesita: la configuración y la base."""
    def __init__(self):
        self.config = {k: getattr(Config, k) for k in dir(Config)
                       if k.isupper()}
        self.supabase = SupabaseAPI(os.getenv('SUPABASE_URL', ''),
                                    os.getenv('SUPABASE_KEY', ''))


def comprobar_tabla(app):
    """¿Está aplicada la migración 030?"""
    url = f'{app.supabase.url}/rest/v1/vencimiento_avisos?select=id&limit=1'
    try:
        r = app.supabase._session.get(url, timeout=(4, 10))
    except Exception as e:
        print(f'{MAL} No se pudo consultar la base: {e}')
        return False
    if r.status_code == 200:
        print(f'{OK} La tabla `vencimiento_avisos` existe.')
        return True
    if r.status_code == 404:
        print(f'{MAL} No existe la tabla `vencimiento_avisos`.')
        print('         Aplica migrations/030_calendario_de_vencimientos.sql en:')
        print(f'         {_panel_sql(app)}')
        return False
    print(f'{MAL} La base respondió HTTP {r.status_code}: {r.text[:120]}')
    return False


def comprobar_indices(app):
    """Los índices no se pueden listar desde PostgREST, pero sí se nota si
    faltan: la consulta que hace la revisión diaria se vuelve lenta."""
    inicio = time.monotonic()
    try:
        datos = avisos.incumplidos(app)
    except avisos.ConsultaFallida as e:
        print(f'{MAL} La base no contestó: {e}')
        print('         Casi siempre son los índices de la 030: sin ellos '
              'PostgreSQL cancela la consulta.')
        print('         Ojo: esto NO significa que no haya incumplimientos, '
              'significa que no se pudo mirar.')
        return -1
    tardanza = time.monotonic() - inicio
    total = len(datos['proyectos']) + len(datos['tareas'])
    if tardanza > 3:
        print(f'{AVISO} La consulta de vencimientos tardó {tardanza:.1f} s. '
              'Suele significar que faltan los índices de la 030;')
        print('         en frío PostgreSQL la cancela y el sistema entiende '
              '«no se incumple nada».')
    else:
        print(f'{OK} La consulta de vencimientos responde en {tardanza:.2f} s.')
    print(f'         Ahora mismo hay {len(datos["proyectos"])} proyecto(s) y '
          f'{len(datos["tareas"])} actividad(es) fuera de plazo.')
    return total


def comprobar_correo(app):
    """Que esté configurado y que el servidor acepte la clave. Configurado no
    es lo mismo que funcionando: una contraseña de aplicación mal copiada pasa
    la primera comprobación y falla en la segunda."""
    cf = avisos._conf(app)
    if not cf['host'] or not cf['remitente']:
        print(f'{MAL} El correo no está configurado.')
        print('         Faltan SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM '
              'en el .env del servidor.')
        return False
    print(f'{OK} Configurado: {cf["remitente"]} vía {cf["host"]}:{cf["port"]} '
          f'({"SSL" if cf["ssl"] else "STARTTLS"}).')
    try:
        if cf['ssl']:
            servidor = smtplib.SMTP_SSL(cf['host'], cf['port'], timeout=20)
        else:
            servidor = smtplib.SMTP(cf['host'], cf['port'], timeout=20)
        with servidor:
            if not cf['ssl']:
                servidor.starttls()
            if cf['user']:
                servidor.login(cf['user'], cf['password'])
        print(f'{OK} El servidor de correo aceptó la conexión y la clave.')
        return True
    except smtplib.SMTPAuthenticationError:
        print(f'{MAL} El servidor rechazó la clave.')
        print('         Con Gmail hace falta una CONTRASEÑA DE APLICACIÓN de 16 '
              'letras, no la de la cuenta:')
        print('         https://myaccount.google.com/apppasswords')
        return False
    except Exception as e:
        print(f'{MAL} No se pudo hablar con el servidor de correo: {e}')
        return False


def _panel_sql(app):
    """La dirección del editor SQL del proyecto, sacada de la propia URL."""
    ref = (app.supabase.url or '').replace('https://', '').split('.')[0]
    return f'https://supabase.com/dashboard/project/{ref}/sql'


def main():
    app = AppMinima()
    if not app.supabase.url:
        print(f'{MAL} No hay SUPABASE_URL en el entorno. ¿Falta el .env?')
        return 1

    print('\n=== Calendario de vencimientos: comprobación ===\n')
    print('1. Memoria de los avisos (migración 030)')
    tabla = comprobar_tabla(app)

    print('\n2. Detección de incumplimientos')
    pendientes = comprobar_indices(app)

    print('\n3. Salida del correo')
    correo = comprobar_correo(app)

    if '--enviar' in sys.argv:
        print('\n4. Envío de prueba')
        if not correo:
            print(f'{MAL} No se intenta: el correo no está en pie.')
        else:
            r = avisos.revisar_vencimientos(app, forzar=True)
            if r.get('enviado'):
                print(f'{OK} Correo enviado a {r["destino"]} con '
                      f'{r.get("proyectos", 0)} proyecto(s) y '
                      f'{r.get("actividades", 0)} actividad(es).')
            elif r.get('success'):
                print(f'{AVISO} No se envió nada porque no hay ningún plazo '
                      'incumplido. Es la respuesta correcta.')
            else:
                print(f'{MAL} {r.get("error")}')

    print('\n=== Resumen ===')
    if pendientes < 0:
        print('  La base no responde a la consulta de vencimientos. Hasta que '
              'eso se arregle')
        print('  el aviso no puede funcionar, y el sistema lo dice en vez de '
              'callárselo.')
        return 1
    if tabla and correo:
        hora = avisos._conf(app)['hora']
        print(f'  Todo en pie. La revisión corre cada día a las {hora:02d}:00 '
              '(hora de Guayaquil)')
        print(f'  y avisa a {avisos._conf(app)["destino"]} de lo que esté '
              'incumplido.')
        return 0
    print('  Falta algo de lo de arriba. Hasta que se cierre, el sistema '
          'detecta los')
    print('  incumplimientos y los enseña en pantalla, pero no sale el correo.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
