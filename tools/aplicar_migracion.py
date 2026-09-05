#!/usr/bin/env python3
# =============================================================================
# aplicar_migracion.py
# Desarrollado por Marco Antonio Posligua San Martín
#
# Aplica los archivos de migrations/ contra la base de producción, deja
# constancia de lo aplicado y sabe decir qué falta por aplicar.
#
# Por qué existe: las migraciones de este proyecto se aplicaban a mano,
# copiando el SQL y pegándolo en un editor. Funciona, pero deja el despliegue
# partido en dos —el código se sube con un push y la base se toca a mano— y
# basta olvidar el segundo paso para que la versión nueva salga a producción
# contra una base vieja.
#
# Y eso fue exactamente lo que pasó: la 030, la 032, la 033 y la 034 estuvieron
# sin aplicar durante semanas y NADA lo delataba. Dos motivos, los dos
# arreglados aquí:
#
#   1. Esta herramienta sólo sabía hablar con api.supabase.com, la API de
#      gestión de supabase.com. La base de producción NO está en supabase.com:
#      es un PostgreSQL propio (contable-supabase-db-1, en el servidor), con
#      PostgREST delante en supabase-ca.pensamiento-libre.org. O sea que la
#      herramienta no podía aplicar nada, y quien la ejecutara se quedaba con
#      un error que parecía de credenciales.
#      Ahora el camino normal es la conexión DIRECTA a PostgreSQL
#      (DATABASE_URL o PG_ADMIN_URL). La ruta de api.supabase.com queda sólo
#      como respaldo, y sólo si SUPABASE_URL apunta a *.supabase.co.
#
#   2. No había registro de lo aplicado, así que no existía forma de preguntar
#      «¿qué falta?». Desde la 035 hay una tabla `schema_migrations`: cada
#      migración que se aplica se anota ahí, lo ya anotado se salta, y
#      `--estado` enseña la lista entera con lo que queda pendiente.
#
# La clave de servicio (SUPABASE_KEY) NO sirve para esto: habla con PostgREST,
# que ejecuta consultas sobre tablas, no CREATE TABLE. Hace falta la cadena de
# conexión de PostgreSQL, con un usuario que pueda crear tablas (postgres).
#
# Uso:
#   python tools/aplicar_migracion.py --estado           # qué hay y qué falta
#   python tools/aplicar_migracion.py --pendientes       # aplica todo lo que falte
#   python tools/aplicar_migracion.py migrations/035_*.sql
#   python tools/aplicar_migracion.py --ver migrations/035_*.sql   # sin aplicar
#   python tools/aplicar_migracion.py --forzar migrations/035_*.sql  # aunque esté anotada
#
# Cada archivo se aplica dentro de UNA transacción: o entra entero o no entra
# nada. La anotación en `schema_migrations` va en esa misma transacción, para
# que no pueda quedar apuntado lo que no se aplicó ni aplicado lo que no se
# apuntó.
# =============================================================================
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv                               # noqa: E402
load_dotenv()

API = 'https://api.supabase.com/v1'
CARPETA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'migrations')

# Las que se aplicaron a mano antes de que existiera el registro. La 035 las
# siembra en la tabla; esto es sólo para que `--estado` no mienta mientras la
# 035 todavía no se ha aplicado.
TABLA_REGISTRO = 'schema_migrations'


def conexion_directa():
    """La cadena de PostgreSQL, si la hay. Es el camino bueno."""
    for nombre in ('DATABASE_URL', 'PG_ADMIN_URL'):
        valor = os.getenv(nombre, '').strip()
        if valor:
            return nombre, valor
    return None, None


def sin_contrasena(dsn):
    """Para poder imprimir a dónde nos conectamos sin soltar la contraseña."""
    return re.sub(r'://([^:/@]+):[^@]*@', r'://\1:***@', dsn)


def abrir(dsn):
    """Devuelve (conexión, None) o (None, explicación de por qué no se pudo).

    El ImportError pelado de psycopg no le dice nada a nadie: aquí se traduce
    a la línea de pip que hay que teclear.
    """
    try:
        import psycopg                                       # noqa: F401
        motor = psycopg
    except ImportError:
        try:
            # En la PC de desarrollo puede estar sólo el psycopg2 de siempre.
            # Para lo que hacemos aquí —abrir, ejecutar y confirmar— sirve
            # igual, así que no se obliga a instalar nada si ya hay con qué.
            import psycopg2 as motor                          # noqa: F401
        except ImportError:
            return None, ('Falta el conector de PostgreSQL. Instálalo con:\n'
                          '    pip install "psycopg[binary]"\n'
                          '(está en requirements.txt; si trabajas con un\n'
                          ' entorno virtual, actívalo antes)')
    try:
        conexion = motor.connect(dsn)
    except Exception as e:
        return None, f'No se pudo conectar a PostgreSQL: {e}'
    conexion.autocommit = False          # todo va dentro de una transacción
    return conexion, None


def version_de(ruta):
    """El número de la migración: 034_los_quipux....sql → '034'.

    Los archivos sin número al principio (planning_tables.sql y compañía) se
    quedan con su nombre entero: se pueden aplicar a mano, pero no entran en
    la cuenta de pendientes, porque no hay orden que respetar en ellos.
    """
    nombre = os.path.basename(ruta)
    if nombre.lower().endswith('.sql'):
        nombre = nombre[:-4]
    m = re.match(r'^(\d{3})_', nombre)
    return m.group(1) if m else nombre


def numeradas():
    """Las migraciones con número, en orden."""
    archivos = glob.glob(os.path.join(CARPETA, '*.sql'))
    con_numero = [a for a in archivos if re.match(r'^\d{3}_', os.path.basename(a))]
    return sorted(con_numero, key=lambda a: os.path.basename(a))


def aplicadas(conexion):
    """Lo que ya está anotado. None si la tabla todavía no existe (pre-035)."""
    with conexion.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f'public.{TABLA_REGISTRO}',))
        if cur.fetchone()[0] is None:
            conexion.rollback()
            return None
        cur.execute(f'SELECT version FROM {TABLA_REGISTRO}')
        filas = {f[0] for f in cur.fetchall()}
    conexion.rollback()                  # sólo hemos leído; no dejamos nada abierto
    return filas


def aplicar_archivo(conexion, ruta, ya, forzar=False):
    """Aplica un .sql y lo anota. Devuelve (aplicada?, mensaje)."""
    version = version_de(ruta)
    if ya is not None and version in ya and not forzar:
        return False, f'  {version}  ya estaba aplicada, se salta'

    sql = open(ruta, encoding='utf-8').read()
    try:
        with conexion.cursor() as cur:
            cur.execute(sql)
            # La anotación viaja en la MISMA transacción que el SQL: si algo
            # falla, no queda apuntado lo que no entró. Si la tabla acaba de
            # nacer en este mismo archivo (la 035), el INSERT la ve igual.
            cur.execute(
                f'INSERT INTO {TABLA_REGISTRO} (version) VALUES (%s) '
                'ON CONFLICT (version) DO NOTHING', (version,))
        conexion.commit()
    except Exception as e:
        conexion.rollback()
        return False, f'  {version}  FALLÓ (no se aplicó nada de este archivo): {e}'
    return True, f'  {version}  aplicada'


def estado(conexion):
    ya = aplicadas(conexion)
    if ya is None:
        print(f'La tabla {TABLA_REGISTRO} todavía no existe: aplica primero')
        print('  python tools/aplicar_migracion.py migrations/035_registro_de_migraciones.sql')
        print('(esa migración siembra como aplicadas las versiones 001..034,')
        print(' que es como está la base de producción hoy)')
        return 1
    pendientes = []
    for ruta in numeradas():
        v = version_de(ruta)
        marca = 'aplicada' if v in ya else 'PENDIENTE'
        if v not in ya:
            pendientes.append(ruta)
        print(f'  {v}  {marca}   {os.path.basename(ruta)}')
    print()
    if pendientes:
        print(f'Faltan {len(pendientes)} por aplicar. Se aplican todas con:')
        print('  python tools/aplicar_migracion.py --pendientes')
        return 1
    print('Todo aplicado.')
    return 0


def por_la_api_de_supabase(ruta, sql):
    """Respaldo para proyectos alojados en supabase.com.

    No sirve para la base de producción de este sistema (que es un PostgreSQL
    propio), pero se conserva por si algún día vuelve a haber un proyecto en
    supabase.co. Aquí no hay registro de migraciones: la API de gestión no
    devuelve un cursor con el que anotar de forma fiable.
    """
    import requests
    url = os.getenv('SUPABASE_URL', '')
    ref = url.replace('https://', '').split('.')[0]
    token = os.getenv('SUPABASE_ACCESS_TOKEN', '')
    if not token:
        print('Falta SUPABASE_ACCESS_TOKEN.')
        print('  1. Créalo en https://supabase.com/dashboard/account/tokens')
        print('  2. Añádelo al .env (que está fuera de git y de la imagen Docker)')
        print('  3. Cuando termines, revócalo: abre TODOS los proyectos de la cuenta.')
        return 1
    print(f'Proyecto alojado en supabase.com: {ref}')
    try:
        r = requests.post(f'{API}/projects/{ref}/database/query',
                          headers={'Authorization': f'Bearer {token}',
                                   'Content-Type': 'application/json'},
                          json={'query': sql}, timeout=120)
    except Exception as e:
        print(f'No se pudo hablar con Supabase: {e}')
        return 1
    if r.status_code in (200, 201):
        print(f'Aplicada correctamente: {os.path.basename(ruta)}')
        return 0
    if r.status_code in (401, 403):
        print(f'Supabase rechazó el token (HTTP {r.status_code}).')
        print('¿Está bien copiado y no caducado? Empieza por «sbp_».')
        return 1
    print(f'Supabase devolvió HTTP {r.status_code}:')
    print(r.text[:800])
    return 1


def falta_la_cadena():
    print('No hay cadena de conexión a PostgreSQL.')
    print('La base de producción es un PostgreSQL propio (no supabase.com), así')
    print('que las migraciones se aplican conectándose a él directamente.')
    print()
    print('Pon en el .env UNA de estas dos (son lo mismo; la segunda existe')
    print('para dejar claro que es la de administrar, no la de la aplicación):')
    print('  DATABASE_URL=postgresql://postgres:CLAVE@HOST:5432/calendario')
    print('  PG_ADMIN_URL=postgresql://postgres:CLAVE@HOST:5432/calendario')
    print()
    print('La clave es la del PostgreSQL del servidor (contable-supabase-db-1),')
    print('no la de PostgREST ni la de la aplicación. Si el puerto no está')
    print('abierto hacia fuera, se abre un túnel primero:')
    print('  ssh -L 5433:localhost:5432 root@178.104.101.84')
    print('  DATABASE_URL=postgresql://postgres:CLAVE@localhost:5433/calendario')
    return 2


def main():
    argumentos = [a for a in sys.argv[1:] if not a.startswith('--')]
    solo_ver = '--ver' in sys.argv
    forzar = '--forzar' in sys.argv
    ver_estado = '--estado' in sys.argv
    pendientes = '--pendientes' in sys.argv

    if not argumentos and not (ver_estado or pendientes):
        print('Uso:')
        print('  python tools/aplicar_migracion.py --estado')
        print('  python tools/aplicar_migracion.py --pendientes')
        print('  python tools/aplicar_migracion.py migrations/0XX_*.sql')
        return 2

    # --ver no toca la base: sólo enseña el SQL. Va antes que nada para poder
    # revisar una migración sin tener configurada la conexión.
    if solo_ver:
        ruta = argumentos[0]
        if not os.path.exists(ruta):
            print(f'No existe el archivo {ruta}')
            return 2
        print(f'Migración : {ruta}')
        print(open(ruta, encoding='utf-8').read())
        return 0

    nombre_var, dsn = conexion_directa()

    if not dsn:
        # Respaldo: sólo tiene sentido si la base está de verdad en supabase.com.
        url = os.getenv('SUPABASE_URL', '')
        if url.endswith('.supabase.co') or '.supabase.co/' in url:
            if ver_estado or pendientes:
                print('--estado y --pendientes necesitan conexión directa a')
                print('PostgreSQL: la API de gestión no lleva registro.')
                return 2
            ruta = argumentos[0]
            if not os.path.exists(ruta):
                print(f'No existe el archivo {ruta}')
                return 2
            return por_la_api_de_supabase(ruta, open(ruta, encoding='utf-8').read())
        return falta_la_cadena()

    conexion, problema = abrir(dsn)
    if not conexion:
        print(problema)
        return 1

    print(f'Base      : {sin_contrasena(dsn)}  (desde {nombre_var})')

    try:
        if ver_estado:
            return estado(conexion)

        ya = aplicadas(conexion)
        if ya is None:
            # Antes de la 035 no hay registro. Se avisa, pero no se impide
            # aplicar: la propia 035 es la que crea la tabla.
            print(f'(todavía no existe {TABLA_REGISTRO}: no se puede saltar nada)')

        if pendientes:
            if ya is None:
                print('Aplica primero la 035, que es la que crea el registro:')
                print('  python tools/aplicar_migracion.py migrations/035_registro_de_migraciones.sql')
                return 2
            faltan = [r for r in numeradas() if version_de(r) not in ya]
            if not faltan:
                print('No falta ninguna migración por aplicar.')
                return 0
            print(f'Por aplicar: {len(faltan)}')
            malas = 0
            for ruta in faltan:
                bien, mensaje = aplicar_archivo(conexion, ruta, ya)
                print(mensaje)
                if not bien:
                    malas += 1
                    break            # el orden importa: si una falla, se para
            return 1 if malas else 0

        ruta = argumentos[0]
        if not os.path.exists(ruta):
            print(f'No existe el archivo {ruta}')
            return 2
        print(f'Migración : {ruta}')
        bien, mensaje = aplicar_archivo(conexion, ruta, ya, forzar=forzar)
        print(mensaje)
        if bien:
            print('\nComprueba el resultado con:')
            print('  python tools/aplicar_migracion.py --estado')
        # Saltarse una ya aplicada no es un error: se sale con 0.
        return 0 if (bien or 'ya estaba' in mensaje) else 1
    finally:
        conexion.close()


if __name__ == '__main__':
    sys.exit(main())
