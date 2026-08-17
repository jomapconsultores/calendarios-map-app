#!/usr/bin/env python3
# =============================================================================
# revisar_visibilidad.py
# Desarrollado por Marco Antonio Posligua San Martín
#
# Impide que vuelva a colarse el fallo de «52 incumplidas y ninguna a la vista».
#
# Qué pasó: el filtro que decide qué actividades se ven descarta las que en su
# día bajaron del To-Do de Microsoft, y para eso mira la columna `source`. Dos
# consultas pedían la tabla `tasks` con una lista corta de columnas en la que
# `source` no estaba. El filtro no falla en ese caso —lee None, que no es
# 'ms_todo'— así que las dejaba pasar: el panel contaba 52 incumplidas y 36 de
# ellas eran correos marcados de Outlook. La lista, que sí pedía todas las
# columnas, no enseñaba ninguna. Mismo dato, dos cifras, y ninguna alarma.
#
# Un filtro que no puede distinguir «no es del To-Do» de «no me trajeron la
# columna» es un filtro que falla hacia el lado equivocado, y en silencio.
#
# LA REGLA: si una consulta a `tasks` pide `due_date`, está contando o listando
# plazos, y entonces tiene que pedir también `source`. Las consultas que sólo
# comprueban de quién es una fila (id, created_by, assigned_to…) no llevan
# due_date y no les hace falta.
#
# Lo normal es no tener que pensar en esto: para leer actividades está
# `leer_tareas()`, que añade sola las columnas del filtro. Esta comprobación es
# la red por si alguien vuelve a consultar la tabla por su cuenta.
#
# Uso:  python tools/revisar_visibilidad.py
# Sale con 1 si encuentra una consulta que se deja `source` fuera.
# =============================================================================
import ast
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVOS = ('app/__init__.py', 'app/cronograma.py', 'app/directorio.py',
            'app/avisos.py', 'app/atlas_sync.py')

TABLA = 'tasks'
OBLIGATORIA = 'source'
DISPARADORA = 'due_date'


def _texto(nodo):
    """El valor de un literal de texto, aunque esté partido en varios trozos
    ('a,b' 'c,d') o unido con +. Devuelve None si no es analizable."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        izq, der = _texto(nodo.left), _texto(nodo.right)
        return None if izq is None or der is None else izq + der
    return None


def _es_consulta_de_tareas(llamada):
    """¿Es un .get('tasks', ...) / .get_in('tasks', ...) / .get_q('tasks', ...)?"""
    if not isinstance(llamada.func, ast.Attribute):
        return False
    if llamada.func.attr not in ('get', 'get_in', 'get_q'):
        return False
    if not llamada.args:
        return False
    return _texto(llamada.args[0]) == TABLA


def revisar(ruta):
    fallos = []
    origen = open(ruta, encoding='utf-8').read()
    arbol = ast.parse(origen, filename=ruta)
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call) or not _es_consulta_de_tareas(nodo):
            continue
        select = next((k.value for k in nodo.keywords if k.arg == 'select'), None)
        if select is None:
            continue
        columnas = _texto(select)
        if columnas is None:
            # Se construye en tiempo de ejecución (es lo que hace leer_tareas,
            # que precisamente añade las columnas que faltan). No hay nada que
            # revisar aquí y no se inventa un fallo.
            continue
        if columnas.strip() == '*':
            continue
        pedidas = {c.strip() for c in columnas.split(',')}
        if DISPARADORA in pedidas and OBLIGATORIA not in pedidas:
            fallos.append((nodo.lineno, columnas))
    return fallos


def main():
    total = 0
    for relativa in ARCHIVOS:
        ruta = os.path.join(RAIZ, relativa)
        if not os.path.exists(ruta):
            continue
        for linea, columnas in revisar(ruta):
            total += 1
            print(f'ERROR  {relativa}:{linea}')
            print(f'       la consulta a `{TABLA}` pide `{DISPARADORA}` pero no '
                  f'`{OBLIGATORIA}`:')
            print(f'       select=\'{columnas}\'')
            print('       Sin `source` el filtro de visibilidad deja pasar lo que')
            print('       bajó del To-Do y las cifras dejan de cuadrar con la lista.')
            print('       Usa leer_tareas(), o añade `source` al select.')
    if total:
        print(f'\n{total} consulta(s) sin `{OBLIGATORIA}`.')
        return 1
    print(f'Consultas a `{TABLA}`: todas las que miran plazos piden `{OBLIGATORIA}`.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
