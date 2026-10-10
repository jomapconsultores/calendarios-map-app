# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Que el correo se lea solo cada día, sin tener que acordarse.

Una tarea programada de Windows a las 08:00 —media hora después de la del
Quipux, para que los quipux de la mañana ya estén recogidos cuando se relacione
el correo— que hace `python -m correo diario`: trae lo nuevo, vuelve a
relacionar, publica en la plataforma y deja escrita la página.

La tarea lanza un .cmd y no Python directamente: las tareas de Windows arrancan
en System32, y desde allí `-m correo` no encuentra el paquete. El .cmd se
coloca primero en la carpeta del proyecto. Lo que dice cada mañana queda en
`diario.log`, junto a la base del correo.

No guarda ninguna contraseña: las credenciales siguen donde estaban.
"""
import os
import subprocess

from . import almacen

NOMBRE_TAREA = 'Correo - leer las nueve cuentas'
HORA_DIARIA = '08:00'


def _lanzador():
    proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(proyecto, 'correo', 'CORREO_DIARIO.cmd')
    registro = os.path.join(almacen.carpeta_por_defecto(), 'diario.log')
    contenido = (
        '@echo off\r\n'
        'rem  La pasada diaria del correo. Generado por: py -m correo instalar\r\n'
        'cd /d "%~dp0.."\r\n'
        f'echo ===== %date% %time% >> "{registro}"\r\n'
        f'py -m correo diario >> "{registro}" 2>&1\r\n'
    )
    os.makedirs(os.path.dirname(registro), exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(contenido)
    return ruta, registro


def instalar():
    if os.name != 'nt':
        print('Esto crea una tarea de Windows; en otro sistema no hay nada que instalar.')
        return 1
    lanzador, registro = _lanzador()
    try:
        subprocess.run(
            ['schtasks', '/Create', '/F', '/SC', 'DAILY', '/ST', HORA_DIARIA,
             '/TN', NOMBRE_TAREA, '/TR', f'"{lanzador}"'],
            check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as e:
        salida = (e.stderr or e.stdout or b'').decode('cp1252', 'ignore')
        print(f'No se pudo crear la tarea: {salida.strip()[:200]}')
        return 1
    print(f'Tarea diaria  : «{NOMBRE_TAREA}» a las {HORA_DIARIA}')
    print(f'Lanzador      : {lanzador}')
    print(f'Registro      : {registro}')
    print('\nPara probarla ahora:  schtasks /Run /TN "%s"' % NOMBRE_TAREA)
    return 0


def desinstalar():
    subprocess.run(['schtasks', '/Delete', '/F', '/TN', NOMBRE_TAREA],
                   check=False, capture_output=True, timeout=30)
    print('Quitada la tarea diaria. Lo guardado sigue en ' + almacen.carpeta_por_defecto())
    return 0
