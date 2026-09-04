# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los comandos:

    python -m quipux alta        da de alta la credencial (una sola vez)
    python -m quipux probar      comprueba que se puede entrar, sin bajar nada
    python -m quipux             la pasada completa
    python -m quipux abrir       la pasada y luego abre CuencaDOC en el navegador
    python -m quipux instalar    deja el acceso directo y la tarea diaria

`abrir` es la que se usa a diario: hace el trabajo y deja el navegador en la
misma página de siempre. Quien lo usa no tiene que acordarse de nada — abre
CuencaDOC como todos los días y, de paso, la computadora ya se ha traído lo
nuevo y ha puesto los plazos en el cronograma.
"""
import sys

from . import credenciales
from .sesion import ErrorQuipux, Quipux

URL = 'https://dq.cuenca.gob.ec/index_frames.php'


def _alta():
    return 0 if credenciales.pedir_por_consola() else 1


def _probar():
    """Entra, dice quién es y qué áreas y bandejas ve. No descarga nada.

    Sirve para separar «no funciona» en sus tres posibles causas: la credencial,
    el servidor del municipio, o esta herramienta."""
    q = Quipux()
    q.entrar()
    print(f'\nDentro como: {q.nombre}\n')
    perfiles = q.perfiles()
    print(f'Áreas ({len(perfiles)}):')
    for p in perfiles:
        marca = '→' if p['activo'] else ' '
        tipo = '' if p['institucional'] else '  (sin bandeja institucional)'
        print(f"  {marca} {p['area']}{tipo}")
    print()
    for p in perfiles:
        if not p['institucional']:
            continue
        if not p['activo'] and not q.cambiar_perfil(p['id']):
            print(f"  {p['area']}: NO se pudo entrar a esta área")
            continue
        print(f"  {p['area']}:")
        try:
            for b in q.bandejas():
                cuenta = '' if b['esperados'] is None else f" — {b['esperados']} documento(s)"
                print(f"      {b['nombre']}{cuenta}")
        except Exception as e:
            print(f'      no se pudo leer el menú: {str(e)[:120]}')
    q.salir()
    return 0


def _pasada(abrir_despues=False, limite=None):
    from .recolector import ejecutar
    r = ejecutar(limite=limite)
    print('\n' + '=' * 60)
    print(f"Documentos       : {r['documentos']}  ({r['nuevos']} nuevos)")
    print(f"Con plazo        : {r['con_plazo']}")
    print(f"Adjuntos         : {r['adjuntos']}")
    print(f"Carpeta          : {r['destino']}")
    if r.get('indices', {}).get('html'):
        print(f"Índice           : {r['indices']['html']}")
    crono = r.get('cronograma') or {}
    if crono:
        if crono.get('error'):
            print(f"Cronograma       : no se pudo — {crono['error']}")
        else:
            print(f"Cronograma       : {crono['creadas']} nueva(s), "
                  f"{crono['actualizadas']} con plazo cambiado, "
                  f"{crono['respetadas']} respetada(s)")
    if r['fallos']:
        print(f"\nCon problemas ({len(r['fallos'])}):")
        for f in r['fallos'][:15]:
            print(f'  · {f}')
        if len(r['fallos']) > 15:
            print(f'  … y {len(r["fallos"]) - 15} más')
    print(f"Tardó            : {r['segundos']} s")
    print('=' * 60)
    if abrir_despues:
        import webbrowser
        webbrowser.open(URL)
    return 0


def _instalar():
    from .instalar import instalar
    return instalar()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    orden = (argv[0].lower() if argv else '')
    try:
        if orden == 'alta':
            return _alta()
        if orden in ('probar', 'test'):
            return _probar()
        if orden == 'instalar':
            return _instalar()
        if orden == 'abrir':
            return _pasada(abrir_despues=True)
        limite = None
        if orden == 'muestra':
            limite = int(argv[1]) if len(argv) > 1 else 5
        return _pasada(limite=limite)
    except ErrorQuipux as e:
        print(f'\nNo se pudo: {e}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('\nInterrumpido. Lo descargado hasta ahora se queda donde está.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
