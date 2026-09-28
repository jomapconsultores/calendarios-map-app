# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Las migraciones se aplican al desplegar, no cuando alguien se acuerda.

El despliegue estaba partido en dos: el código sube con un push y la base se
tocaba a mano después. Basta olvidar el segundo paso para que la versión nueva
salga a producción contra una base vieja, y eso no se nota hasta que algo falla
por una columna que no existe. Ya pasó: la 030, la 032, la 033 y la 034
estuvieron sin aplicar durante semanas y NADA lo delataba (ver la 040).

Aquí se cierra ese hueco. Al arrancar, la aplicación mira qué migraciones
están anotadas en `schema_migrations`, aplica las que falten en orden y anota
cada una en la MISMA transacción que su SQL.

Tres cautelas, que son lo que hace que esto se pueda dejar suelto en
producción:

  1. **Si no existe `schema_migrations`, no se aplica nada.** Sin registro no
     hay forma de saber qué está puesto, y aplicar cuarenta migraciones a
     ciegas sobre una base que ya las tiene puede ser destructivo. En ese caso
     se avisa y se sigue: la 040 se aplica a mano una vez, y de ahí en
     adelante esto se sostiene solo.
  2. **Un solo proceso a la vez.** Gunicorn levanta varios trabajadores y
     todos arrancan juntos; sin un cerrojo, dos aplicarían la misma migración
     a la vez. Se pide un advisory lock de PostgreSQL y quien no lo consigue
     se aparta: el que lo tiene hará el trabajo.
  3. **Nunca tumba el arranque.** Si una migración falla, se deshace esa sola
     —su transacción—, se escribe en el registro del servidor y la aplicación
     sigue levantando. Una base a medio migrar es un problema; una aplicación
     que no arranca y no dice por qué, son dos.

Esto NO sustituye a `tools/aplicar_migracion.py`, que sigue siendo la forma de
aplicarlas a mano, mirar el estado y forzar una concreta. Aquí sólo está lo
imprescindible para el arranque, escrito aparte a propósito: lo que corre solo
en cada despliegue tiene que ser corto y fácil de leer entero.
"""
import glob
import os
import re

CARPETA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'migrations')
TABLA_REGISTRO = 'schema_migrations'

# La llave del cerrojo. Es un número cualquiera, pero tiene que ser SIEMPRE el
# mismo: dos procesos que pidan llaves distintas no se estorban, que es justo
# lo que aquí no se quiere.
LLAVE_CERROJO = 4210042


def version_de(ruta):
    """El número de la migración: 042_los_encargados....sql → '042'."""
    nombre = os.path.basename(ruta)
    if nombre.lower().endswith('.sql'):
        nombre = nombre[:-4]
    m = re.match(r'^(\d{3})_', nombre)
    return m.group(1) if m else nombre


def numeradas():
    """Las migraciones con número, en orden. Las demás no entran: se aplican a
    mano y no hay orden que respetar en ellas."""
    archivos = glob.glob(os.path.join(CARPETA, '*.sql'))
    return sorted((a for a in archivos
                   if re.match(r'^\d{3}_', os.path.basename(a))),
                  key=lambda a: os.path.basename(a))


def cadena_a_usar(dsn_app=None):
    """Con qué credencial se aplican. PG_ADMIN_URL manda si está.

    La cadena de la aplicación sirve para leer y escribir FILAS, que es su
    trabajo, pero no para cambiar la FORMA de las tablas: `app_calendario` no
    es dueño de ellas y PostgreSQL contesta «must be owner of table». Una
    migración que añade una columna necesita a alguien que pueda, y ése es el
    dueño (normalmente `postgres`), que se pone en PG_ADMIN_URL.

    Es a propósito que sean dos: la aplicación corre todo el día con la
    credencial justa, y la que puede rehacer tablas sólo aparece en el arranque
    y sólo si se la configura."""
    admin = (os.getenv('PG_ADMIN_URL') or '').strip()
    return admin or dsn_app


def aplicar_pendientes(dsn, registro=print):
    """Aplica lo que falte. Devuelve un resumen; no lanza nunca.

    `registro` recibe cada línea, para que quede en el log del servidor: el día
    que una migración falle, ese texto es lo único que habrá para saberlo.
    """
    salida = {'aplicadas': [], 'fallos': [], 'pendientes': [], 'aviso': None}
    dsn = cadena_a_usar(dsn)
    if not dsn:
        salida['aviso'] = 'sin DATABASE_URL: no se revisan migraciones'
        return salida
    try:
        import psycopg
    except ImportError:
        salida['aviso'] = 'sin psycopg: no se revisan migraciones'
        return salida

    try:
        con = psycopg.connect(dsn, connect_timeout=10)
    except Exception as e:
        salida['aviso'] = f'no se pudo abrir la base: {str(e)[:150]}'
        registro(f'[migraciones] {salida["aviso"]}')
        return salida

    try:
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)', (LLAVE_CERROJO,))
            if not cur.fetchone()[0]:
                salida['aviso'] = 'otro proceso las está revisando'
                return salida

        try:
            con.autocommit = False
            with con.cursor() as cur:
                cur.execute('SELECT to_regclass(%s)', (f'public.{TABLA_REGISTRO}',))
                existe = cur.fetchone()[0] is not None
            con.rollback()

            if not existe:
                # Sin registro no se sabe qué está puesto, y aplicarlo todo a
                # ciegas sobre una base que ya lo tiene puede romper cosas.
                salida['aviso'] = (
                    f'no existe la tabla {TABLA_REGISTRO}: no se aplica nada. '
                    'Aplica una vez migrations/040_*.sql con '
                    'tools/aplicar_migracion.py y a partir de ahí esto va solo.')
                registro(f'[migraciones] {salida["aviso"]}')
                return salida

            with con.cursor() as cur:
                cur.execute(f'SELECT version FROM {TABLA_REGISTRO}')
                ya = {f[0] for f in cur.fetchall()}
            con.rollback()

            for ruta in numeradas():
                version = version_de(ruta)
                if version in ya:
                    continue
                salida['pendientes'].append(version)
                try:
                    sql = open(ruta, encoding='utf-8').read()
                    with con.cursor() as cur:
                        cur.execute(sql)
                        # La anotación va en la MISMA transacción que el SQL:
                        # así no puede quedar apuntado lo que no entró, ni
                        # aplicado lo que no se apuntó.
                        cur.execute(
                            f'INSERT INTO {TABLA_REGISTRO} (version) VALUES (%s) '
                            'ON CONFLICT (version) DO NOTHING', (version,))
                    con.commit()
                    salida['aplicadas'].append(version)
                    registro(f'[migraciones] {version} aplicada')
                except Exception as e:
                    con.rollback()
                    detalle = str(e)[:300]
                    # «must be owner» no es un fallo de la migración: es que
                    # quien la aplica no puede cambiar tablas. Decirlo a secas
                    # manda a revisar el SQL, que está bien.
                    if 'must be owner' in detalle or 'permission denied' in detalle:
                        detalle += ('  → la credencial con la que corre la '
                                    'aplicación no puede cambiar tablas. Pon '
                                    'PG_ADMIN_URL con el usuario dueño de la '
                                    'base (postgres) y vuelve a desplegar.')
                    salida['fallos'].append((version, detalle))
                    registro(f'[migraciones] {version} FALLÓ, no se aplicó nada '
                             f'de ese archivo: {detalle}')
                    # Se para en la primera que falla: las siguientes suelen
                    # dar por hecho lo que ésta debía dejar hecho, y seguir
                    # sólo convierte un fallo claro en cinco confusos.
                    break
        finally:
            try:
                con.autocommit = True
                with con.cursor() as cur:
                    cur.execute('SELECT pg_advisory_unlock(%s)', (LLAVE_CERROJO,))
            except Exception:
                pass
    except Exception as e:
        salida['aviso'] = f'no se pudieron revisar: {str(e)[:200]}'
        registro(f'[migraciones] {salida["aviso"]}')
    finally:
        try:
            con.close()
        except Exception:
            pass

    if not salida['aplicadas'] and not salida['fallos'] and not salida['aviso']:
        registro('[migraciones] al día')
    return salida
