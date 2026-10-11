# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Que el correo se lea solo, sin tener que acordarse.

Una tarea programada de Windows cada `CADA_MINUTOS` minutos que hace
`python -m correo diario`: trae lo nuevo, vuelve a relacionar, publica en la
plataforma y deja escrita la página. Una vez al día no bastaba: lo que se
marcaba o llegaba por la mañana no se veía en la plataforma hasta el día
siguiente. Una pasada sin nada nuevo tarda medio minuto y sólo pregunta.

La tarea lanza `pyw` —Python sin ventana— con un lanzador que se escribe en la
carpeta del proyecto: las tareas de Windows arrancan en System32, y desde allí
`-m correo` no encuentra el paquete. Sin ventana a propósito: con la consola a
la vista, cerrarla por descuido cortaba la pasada a mitad (código 0xC000013A),
antes de publicar. Lo que dice cada pasada queda en `diario.log`, junto a la
base del correo; pasado `TOPE_REGISTRO` se aparta a `diario.log.anterior` y se
empieza otro, que cada cuarto de hora crecería sin fin.

Es una portátil, y Windows crea las tareas para que sólo corran enchufadas y
sólo a su hora. `_sin_ataduras` le quita las dos cosas —corre con batería, y si
la hora pasó con la máquina apagada, corre en cuanto vuelve— y le pone un tope
de media hora por si una cuenta se cuelga. Mientras una pasada sigue, la
siguiente no arranca.

No guarda ninguna contraseña: las credenciales siguen donde estaban.
"""
import os
import subprocess

from . import almacen

NOMBRE_TAREA = 'Correo - leer las nueve cuentas'
CADA_MINUTOS = 15
TOPE_REGISTRO = 1_000_000  # bytes de diario.log


def _lanzador():
    proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(proyecto, 'correo', 'correo_diario.pyw')
    registro = os.path.join(almacen.carpeta_por_defecto(), 'diario.log')
    contenido = (
        '# La pasada del correo cada 15 minutos, sin ventana. Generado por: py -m correo instalar\n'
        'import os, sys\n'
        'from datetime import datetime\n'
        f'PROYECTO = {proyecto!r}\n'
        f'REGISTRO = {registro!r}\n'
        'os.chdir(PROYECTO)\n'
        'sys.path.insert(0, PROYECTO)\n'
        f'if os.path.exists(REGISTRO) and os.path.getsize(REGISTRO) > {TOPE_REGISTRO}:\n'
        "    os.replace(REGISTRO, REGISTRO + '.anterior')\n"
        "sys.stdout = sys.stderr = open(REGISTRO, 'a', encoding='utf-8', buffering=1)\n"
        "print('=====', datetime.now().strftime('%d/%m/%Y %H:%M:%S'))\n"
        'from correo.__main__ import main\n'
        "sys.exit(main(['diario']))\n"
    )
    os.makedirs(os.path.dirname(registro), exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(contenido)
    # El lanzador de antes, con ventana, ya no lo usa nadie.
    viejo = os.path.join(proyecto, 'correo', 'CORREO_DIARIO.cmd')
    if os.path.exists(viejo):
        os.remove(viejo)
    return ruta, registro


def _pyw():
    """`pyw` del lanzador de Python, o `pythonw` junto al Python que corre esto."""
    import shutil
    import sys
    return (shutil.which('pyw')
            or os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))


def _sin_ataduras():
    """Lo que `schtasks` no sabe poner, con PowerShell. Devuelve si se pudo."""
    orden = (
        '$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries '
        '-DontStopIfGoingOnBatteries -StartWhenAvailable '
        '-MultipleInstances IgnoreNew '
        '-ExecutionTimeLimit (New-TimeSpan -Minutes 30); '
        f"Set-ScheduledTask -TaskName '{NOMBRE_TAREA}' -Settings $s | Out-Null")
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', orden],
                       capture_output=True, timeout=60)
    return r.returncode == 0


def instalar():
    if os.name != 'nt':
        print('Esto crea una tarea de Windows; en otro sistema no hay nada que instalar.')
        return 1
    lanzador, registro = _lanzador()
    try:
        subprocess.run(
            ['schtasks', '/Create', '/F', '/SC', 'MINUTE', '/MO', str(CADA_MINUTOS),
             '/TN', NOMBRE_TAREA, '/TR', f'"{_pyw()}" "{lanzador}"'],
            check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as e:
        salida = (e.stderr or e.stdout or b'').decode('cp1252', 'ignore')
        print(f'No se pudo crear la tarea: {salida.strip()[:200]}')
        return 1
    print(f'Tarea         : «{NOMBRE_TAREA}» cada {CADA_MINUTOS} minutos')
    if _sin_ataduras():
        print('               también con batería, y al volver si a esa hora estaba apagada')
    else:
        print('               OJO: sólo correrá enchufada y a su hora (no se pudo cambiar)')
    print(f'Lanzador      : {lanzador}')
    print(f'Registro      : {registro}')
    print('\nPara probarla ahora:  schtasks /Run /TN "%s"' % NOMBRE_TAREA)
    return 0


def desinstalar():
    subprocess.run(['schtasks', '/Delete', '/F', '/TN', NOMBRE_TAREA],
                   check=False, capture_output=True, timeout=30)
    print('Quitada la tarea. Lo guardado sigue en ' + almacen.carpeta_por_defecto())
    return 0
