# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Lleva a la plataforma lo que esta computadora sabe de los quipux.

El servidor no puede leer la carpeta `06_DESARROLLO/quipux`: está en el disco
de esta computadora y la máquina donde corre la web no la alcanza. No es un
fallo que arreglar, es que no están en el mismo sitio. Por eso la pantalla
enseña 320 documentos aquí y ninguno allá.

Lo que hace esto es mandar la INFORMACIÓN —número, asunto, remitente, área,
estado y plazo— a la tabla `quipux_documentos`, que es de donde leen el
servidor y el teléfono. Los archivos no viajan: siguen donde están y nadie
tiene que subirlos ni arrastrarlos a ninguna parte.

Se puede correr tantas veces como haga falta. La clave es el identificador del
documento, así que una segunda pasada ACTUALIZA las filas en vez de duplicarlas,
y lo que se haya añadido a la carpeta desde la última vez entra sin más.

Hay dos formas de que lleguen, y se elige sola:

  * POR LA APLICACIÓN (lo normal). Se le mandan a la web, que sí alcanza su
    base, y ella los guarda. Necesita APP_URL y CRON_SECRET en el .env.
  * DIRECTO A LA BASE, si esta computadora la alcanza. Necesita SUPABASE_URL y
    SUPABASE_KEY. Hoy no es el caso: la base del servidor sólo se ve desde
    dentro, por el nombre de su contenedor.

Uso:
    py tools/publicar_quipux.py --simular    # qué se subiría, sin tocar nada
    py tools/publicar_quipux.py              # subirlo
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--simular', action='store_true',
                   help='enseña el recuento y no sube nada')
    args = p.parse_args()

    from quipux import cosecha

    donde = cosecha.carpeta()
    if not donde:
        sys.exit('No se encontró la carpeta de los quipux.\n'
                 'Se busca junto a este proyecto (06_DESARROLLO/quipux); '
                 'para otra ruta, pon QUIPUX_COSECHA en el .env.')
    print('Carpeta:  %s' % donde)

    documentos = cosecha.para_publicar()
    if not documentos:
        sys.exit('La carpeta está, pero no se leyó ningún documento de '
                 '%s' % (cosecha.base() or 'data/quipux.db'))

    abiertos = [d for d in documentos if (d.get('estado') or 'abierto') != 'cerrado']
    con_plazo = [d for d in abiertos if (d.get('plazo') or {}).get('fecha')]
    areas = {}
    for d in documentos:
        areas[d.get('area') or '(sin área)'] = areas.get(d.get('area') or '(sin área)', 0) + 1

    print('Documentos:   %d' % len(documentos))
    print('  abiertos:   %d' % len(abiertos))
    print('  realizados: %d' % (len(documentos) - len(abiertos)))
    print('  con plazo:  %d  (de los abiertos, como la pantalla)' % len(con_plazo))
    for a, n in sorted(areas.items()):
        print('  %-16s %d' % (a, n))

    if args.simular:
        print('\n(simulación: no se subió nada)')
        return

    subidos = _mandar(documentos)
    print('\nPublicados %d documento(s). Ya se ven en la web y en el teléfono.'
          % subidos)


def _mandar(documentos):
    """Los deja donde el servidor pueda leerlos. Devuelve cuántos entraron.

    Primero por la aplicación, que es el camino que funciona hoy: la base del
    servidor no se alcanza desde fuera —PostgREST está retirado y el host es el
    de un contenedor de su red interna—, pero la web sí, y ella llega a su
    base. La vía directa se queda como respaldo para el día que esta
    computadora sí tenga acceso."""
    from dotenv import load_dotenv
    load_dotenv()

    destino = (os.getenv('APP_URL') or 'https://calendario.pensamiento-libre.org').rstrip('/')
    secreto = os.getenv('CRON_SECRET') or ''
    if secreto:
        import json
        import urllib.error
        import urllib.request
        cuerpo = json.dumps({'documentos': documentos}).encode('utf-8')
        pet = urllib.request.Request(
            destino + '/quipux/api/publicar', data=cuerpo, method='POST',
            headers={'Content-Type': 'application/json', 'X-Cron-Secret': secreto})
        try:
            with urllib.request.urlopen(pet, timeout=180) as r:
                datos = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode('utf-8', 'ignore')[:300]
            sys.exit('El servidor contestó %s: %s' % (e.code, detalle))
        except Exception as e:
            sys.exit('No se pudo hablar con %s: %s' % (destino, str(e)[:200]))
        if not datos.get('success'):
            sys.exit('No se pudo publicar: %s' % datos.get('error'))
        return datos.get('publicados', 0)

    # Sin CRON_SECRET queda la vía directa, si la base se deja alcanzar.
    from quipux import planificacion
    db = planificacion.cliente_de_la_plataforma()
    if db is None:
        sys.exit('\nNo hay por dónde mandarlo. Pon CRON_SECRET en el .env para '
                 'que entre por la aplicación (es lo que funciona hoy), o '
                 'SUPABASE_URL/SUPABASE_KEY si esta computadora alcanza la base.')

    resultado = planificacion.publicar(db, documentos)
    if resultado.get('error'):
        # Se dice lo que subió ANTES de fallar: no es lo mismo que no entrara
        # nada a que entraran doscientos y se cortara en el lote siguiente.
        print('\nSubidos antes de fallar: %d' % resultado.get('subidos', 0))
        sys.exit('No se pudo terminar: %s' % resultado['error'])
    return resultado.get('subidos', 0)


if __name__ == '__main__':
    main()
