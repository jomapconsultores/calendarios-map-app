#!/usr/bin/env python3
# =============================================================================
# aplicar_migracion.py
# Desarrollado por Marco Antonio Posligua San Martín
#
# Aplica un archivo de migrations/ contra la base de Supabase.
#
# Por qué existe: las migraciones de este proyecto se aplicaban a mano,
# copiando el SQL y pegándolo en el editor del panel de Supabase. Funciona, pero
# deja el despliegue partido en dos —el código se sube con un push y la base se
# toca a mano— y basta olvidar el segundo paso para que la versión nueva salga a
# producción contra una base vieja. Ya pasó con la 030: el código estaba
# desplegado y la tabla no existía.
#
# La clave de servicio (SUPABASE_KEY) NO sirve para esto: habla con PostgREST,
# que ejecuta consultas sobre tablas, no CREATE TABLE. Hace falta un token
# personal (SUPABASE_ACCESS_TOKEN), que es el que usa el propio panel.
#
# El token abre TODOS los proyectos de la cuenta, así que:
#   - se lee del entorno o del .env, nunca se escribe en el repositorio;
#   - conviene revocarlo cuando ya no haga falta, en
#     https://supabase.com/dashboard/account/tokens
#
# Uso:
#   python tools/aplicar_migracion.py migrations/030_calendario_de_vencimientos.sql
#   python tools/aplicar_migracion.py --ver migrations/030_....sql   # sin aplicar
#
# El proyecto se deduce de SUPABASE_URL, para no tener que teclear la referencia
# ni arriesgarse a aplicar una migración contra la base equivocada.
# =============================================================================
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                              # noqa: E402
from dotenv import load_dotenv                               # noqa: E402
load_dotenv()

API = 'https://api.supabase.com/v1'


def referencia_del_proyecto():
    url = os.getenv('SUPABASE_URL', '')
    if not url:
        return None
    return url.replace('https://', '').split('.')[0] or None


def main():
    argumentos = [a for a in sys.argv[1:] if not a.startswith('--')]
    solo_ver = '--ver' in sys.argv
    if not argumentos:
        print('Uso: python tools/aplicar_migracion.py migrations/XXX.sql')
        return 2
    ruta = argumentos[0]
    if not os.path.exists(ruta):
        print(f'No existe el archivo {ruta}')
        return 2
    sql = open(ruta, encoding='utf-8').read()

    ref = referencia_del_proyecto()
    if not ref:
        print('No hay SUPABASE_URL en el entorno: no sé contra qué base aplicar.')
        return 2

    print(f'Migración : {ruta}')
    print(f'Proyecto  : {ref}')
    print(f'Sentencias: {sql.count(";")} (aprox.)\n')

    if solo_ver:
        print(sql)
        return 0

    token = os.getenv('SUPABASE_ACCESS_TOKEN', '')
    if not token:
        print('Falta SUPABASE_ACCESS_TOKEN.')
        print('  1. Créalo en https://supabase.com/dashboard/account/tokens')
        print('  2. Añádelo al .env (que está fuera de git y de la imagen Docker):')
        print('       SUPABASE_ACCESS_TOKEN=sbp_...')
        print('  3. Vuelve a ejecutar esto.')
        print('  4. Cuando termines, revoca el token: abre todos tus proyectos.')
        return 1

    try:
        r = requests.post(f'{API}/projects/{ref}/database/query',
                          headers={'Authorization': f'Bearer {token}',
                                   'Content-Type': 'application/json'},
                          json={'query': sql}, timeout=120)
    except Exception as e:
        print(f'No se pudo hablar con Supabase: {e}')
        return 1

    if r.status_code in (200, 201):
        print('Aplicada correctamente.')
        cuerpo = r.text.strip()
        if cuerpo and cuerpo not in ('[]', '{}'):
            print(f'Respuesta: {cuerpo[:400]}')
        print('\nComprueba el resultado con:')
        print('  python tools/comprobar_vencimientos.py')
        return 0

    if r.status_code in (401, 403):
        print(f'Supabase rechazó el token (HTTP {r.status_code}).')
        print('¿Está bien copiado y no caducado? Empieza por «sbp_».')
        return 1

    print(f'Supabase devolvió HTTP {r.status_code}:')
    print(r.text[:800])
    return 1


if __name__ == '__main__':
    sys.exit(main())
