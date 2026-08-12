#!/usr/bin/env python3
# =============================================================================
# revisar_js_plantillas.py
# Desarrollado por Marco Antonio Posligua San Martín
#
# Revisa la sintaxis del JavaScript que va ESCRITO DENTRO de las plantillas.
#
# Por qué existe: en calendar.html tres «\n» acabaron guardados como saltos de
# línea de verdad. Una cadena así queda sin cerrar, y el navegador no se salta
# esa línea: aborta el bloque <script> ENTERO con «SyntaxError: Invalid or
# unexpected token». Como el render del calendario vivía en ese bloque, la
# rejilla dejó de dibujarse — y con ella el modal de citas, los pendientes y los
# autocompletados. El servidor no se entera de nada: para Python la plantilla es
# texto, y `compileall` la da por buena. Tres caracteres tumbaron el módulo
# entero sin que ninguna comprobación dijera nada.
#
# Cómo lo revisa: renderiza cada plantilla con Jinja —sin arrancar la app ni
# tocar la base—, saca los <script> sin src y se los pasa a `node --check`.
# Se renderiza en vez de leer el archivo en crudo porque las plantillas llevan
# {{ }} y {% %} mezclados con el JS, que node no sabría leer.
#
# El contexto va vacío a propósito: aquí no se comprueba QUÉ dice la página,
# sólo que el JavaScript sea analizable. Cualquier nombre que la plantilla pida
# (url_for, current_user, calendarios...) lo resuelve `Permisiva`, que se deja
# usar como valor, como función, como lista vacía y como objeto.
#
# Uso:  python tools/revisar_js_plantillas.py
# Sale con 1 si algún bloque no compila. Requiere node en el PATH.
# =============================================================================
import json
import os
import re
import subprocess
import sys
import tempfile

from jinja2 import ChainableUndefined, Environment, FileSystemLoader
from markupsafe import Markup

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLAS = os.path.join(RAIZ, 'templates')

# <script> propios de la página. Los que traen src= son de CDN: no es asunto
# nuestro revisarlos, y además aquí no se descargan.
SCRIPT_INLINE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S)

# type="text/template" y compañía guardan HTML dentro de un <script>, no código.
SCRIPT_NO_JS = re.compile(r'type\s*=\s*["\']([^"\']+)["\']', re.I)
TIPOS_JS = {'text/javascript', 'application/javascript', 'module', ''}


class Permisiva(ChainableUndefined):
    """Un valor que aguanta cualquier cosa que la plantilla le haga.

    Jinja, ante un nombre que no existe, devuelve Undefined y revienta en cuanto
    se recorre o se llama. Como aquí el contexto va vacío A PROPÓSITO, eso
    pasaría en casi todas las plantillas. Esta versión se deja llamar como
    función, recorrer como lista vacía e imprimir como cadena vacía, para que el
    render llegue hasta el final y podamos ver el JavaScript."""

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __call__(self, *args, **kwargs):
        return self

    def __getitem__(self, clave):
        return self

    def __str__(self):
        return ''

    def __bool__(self):
        return False


def revisar():
    entorno = Environment(loader=FileSystemLoader(PLANTILLAS),
                          undefined=Permisiva,
                          autoescape=True)
    # `dict` y `range` se usan sueltos en algunas plantillas; el resto lo cubre
    # Permisiva por la vía de los nombres no definidos.
    entorno.globals.update({'dict': dict, 'range': range})
    # `|tojson` es la otra vía por la que un dato entra en el JavaScript
    # (cronograma.html, directorio.html). El tojson de serie revienta con un
    # valor comodín, así que aquí se serializa dejándolo en `null`: para revisar
    # la sintaxis da igual el dato, lo que importa es que la línea exista.
    entorno.filters['tojson'] = lambda valor, **kwargs: Markup(
        json.dumps(valor, default=lambda _: None))

    nombres = sorted(p for p in os.listdir(PLANTILLAS) if p.endswith('.html'))
    if not nombres:
        print('No hay plantillas que revisar en templates/.')
        return 1

    fallos = []
    sin_render = []
    revisados = 0
    tmp = tempfile.mkdtemp(prefix='js-plantillas-')

    for nombre in nombres:
        try:
            html = entorno.get_template(nombre).render()
        except Exception as e:
            # No se da por bueno en silencio: si una plantilla no se puede
            # renderizar, su JavaScript se queda SIN revisar y hay que saberlo.
            sin_render.append((nombre, f'{type(e).__name__}: {e}'))
            continue

        for i, js in enumerate(SCRIPT_INLINE.findall(html)):
            etiqueta = SCRIPT_NO_JS.search(html.split(js)[0][-200:]) if js else None
            if etiqueta and etiqueta.group(1).lower() not in TIPOS_JS:
                continue
            if not js.strip():
                continue
            ruta = os.path.join(tmp, f'{nombre[:-5]}__{i}.js')
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write(js)
            revisados += 1
            p = subprocess.run(['node', '--check', ruta],
                               capture_output=True, text=True)
            if p.returncode != 0:
                fallos.append((nombre, i, p.stderr.strip()))

    for nombre, motivo in sin_render:
        print(f'AVISO  {nombre}: no se pudo renderizar, su JS queda sin revisar '
              f'({motivo})')

    if fallos:
        print()
        for nombre, i, error in fallos:
            print(f'ERROR  templates/{nombre} — bloque <script> #{i + 1}:')
            for linea in error.split('\n'):
                print(f'       {linea}')
            print()
        print(f'{len(fallos)} bloque(s) con error de sintaxis '
              f'de {revisados} revisado(s).')
        return 1

    print(f'{revisados} bloque(s) <script> revisado(s) en {len(nombres)} '
          f'plantilla(s): sintaxis correcta.')
    return 0


if __name__ == '__main__':
    sys.exit(revisar())
