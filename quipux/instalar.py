# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Que se dispare solo al abrir CuencaDOC, sin tener que acordarse.

Lo que se pidió es que esto ocurra «en razón de abrir CuencaDOC». Un sistema
que hay que recordar ejecutar deja de ejecutarse a la segunda semana; el hábito
que ya existe —abrir el navegador en esa página— es el único disparador que no
se olvida.

Se dejan dos cosas, porque cubren huecos distintos:

  1. Un acceso directo «CuencaDOC» en el Escritorio. Al pulsarlo hace la pasada
     y ABRE la página de siempre. El hábito no cambia; sólo pasa a traer el
     trabajo hecho.

  2. Una tarea programada diaria. Porque hay días en que no se abre CuencaDOC
     —y son justo los días en los que un plazo se pasa sin que nadie mire—.

Ninguna de las dos guarda contraseñas: la credencial sigue en el llavero de
Windows, y esto sólo lanza el programa.
"""
import os
import subprocess
import sys

NOMBRE_TAREA = 'CuencaDOC - traer quipux'
HORA_DIARIA = '07:30'


def _rutas():
    proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # El pythonw del entorno del proyecto: sin ventana negra parpadeando.
    candidatos = [
        os.path.join(proyecto, 'venv', 'Scripts', 'pythonw.exe'),
        os.path.join(proyecto, 'venv', 'Scripts', 'python.exe'),
        sys.executable,
    ]
    python = next((c for c in candidatos if os.path.exists(c)), sys.executable)
    return proyecto, python


def _escribir_lanzador(proyecto, python):
    """Un .cmd que hace la pasada y abre la página. Es lo que ejecuta el acceso
    directo, y sirve además para lanzarlo a mano si algo va mal."""
    ruta = os.path.join(proyecto, 'quipux', 'CuencaDOC.cmd')
    consola = python.replace('pythonw.exe', 'python.exe')
    contenido = (
        '@echo off\r\n'
        'rem  Trae los quipux y abre CuencaDOC. Generado por: python -m quipux instalar\r\n'
        f'cd /d "{proyecto}"\r\n'
        'echo Trayendo los quipux de CuencaDOC...\r\n'
        f'"{consola}" -m quipux abrir\r\n'
        'if errorlevel 1 (\r\n'
        '  echo.\r\n'
        '  echo Hubo un problema. La ventana queda abierta para poder leerlo.\r\n'
        '  pause\r\n'
        ')\r\n'
    )
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(contenido)
    return ruta


def _acceso_directo(lanzador):
    """Crea el acceso directo en el Escritorio con PowerShell (sin pywin32)."""
    escritorio = os.path.join(os.path.expanduser('~'), 'Desktop')
    if not os.path.isdir(escritorio):
        escritorio = os.path.join(os.path.expanduser('~'), 'Escritorio')
    if not os.path.isdir(escritorio):
        return None
    destino = os.path.join(escritorio, 'CuencaDOC.lnk')
    ps = (
        '$s = (New-Object -ComObject WScript.Shell).CreateShortcut('
        f'"{destino}"); '
        f'$s.TargetPath = "{lanzador}"; '
        f'$s.WorkingDirectory = "{os.path.dirname(os.path.dirname(lanzador))}"; '
        '$s.Description = "Trae los quipux y abre CuencaDOC"; '
        '$s.IconLocation = "shell32.dll,13"; '
        '$s.Save()'
    )
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                       check=True, capture_output=True, timeout=60)
        return destino
    except Exception as e:
        print(f'  No se pudo crear el acceso directo: {str(e)[:150]}')
        return None


def _tarea_diaria(python, proyecto):
    """La red de seguridad para los días en que no se abre CuencaDOC."""
    orden = f'"{python}" -m quipux'
    try:
        subprocess.run(
            ['schtasks', '/Create', '/F', '/SC', 'DAILY', '/ST', HORA_DIARIA,
             '/TN', NOMBRE_TAREA, '/TR', orden],
            check=True, capture_output=True, timeout=60, cwd=proyecto)
        return True
    except subprocess.CalledProcessError as e:
        salida = (e.stderr or e.stdout or b'').decode('cp1252', 'ignore')
        print(f'  No se pudo crear la tarea diaria: {salida.strip()[:200]}')
        return False
    except Exception as e:
        print(f'  No se pudo crear la tarea diaria: {str(e)[:150]}')
        return False


def instalar():
    if os.name != 'nt':
        print('Esto instala un acceso directo y una tarea de Windows; '
              'en otro sistema no hay nada que instalar.')
        return 1

    proyecto, python = _rutas()
    print('Instalando el disparo automático\n')

    lanzador = _escribir_lanzador(proyecto, python)
    print(f'  Lanzador        : {lanzador}')

    atajo = _acceso_directo(lanzador)
    if atajo:
        print(f'  Acceso directo  : {atajo}')
        print('                    (úsalo en lugar del favorito del navegador)')

    if _tarea_diaria(python, proyecto):
        print(f'  Tarea diaria    : «{NOMBRE_TAREA}» a las {HORA_DIARIA}')

    from . import credenciales
    if not credenciales.hay_credencial():
        print('\n  FALTA la credencial. Dala de alta ahora con:')
        print('      python -m quipux alta')
    print('\nListo.')
    return 0


def desinstalar():
    try:
        subprocess.run(['schtasks', '/Delete', '/F', '/TN', NOMBRE_TAREA],
                       check=False, capture_output=True, timeout=30)
    except Exception:
        pass
    for carpeta in ('Desktop', 'Escritorio'):
        atajo = os.path.join(os.path.expanduser('~'), carpeta, 'CuencaDOC.lnk')
        if os.path.exists(atajo):
            try:
                os.remove(atajo)
            except Exception:
                pass
    print('Quitados el acceso directo y la tarea diaria. '
          'La credencial sigue en el llavero: bórrala con «python -m quipux» si quieres.')
    return 0
