# -*- coding: utf-8 -*-
"""Prueba del almacén del servidor: donde viven los quipux y sus tareas.

Sin servicios externos y sin red: se abre una base de prueba en una carpeta
temporal y se comprueba qué guarda y qué devuelve.

Lo que se vigila:

  * que una segunda pasada ACTUALICE en vez de duplicar — el recolector vuelve
    a ver lo mismo cada día y acumular copias llenaría la lista de la misma
    reunión repetida;
  * que las tareas salgan en el orden en que hay que trabajarlas, no en el de
    llegada;
  * que una tarea que una persona ya cerró NO se reabra porque el Municipio
    siga diciendo la misma fecha;
  * y que un cambio de plazo en CuencaDOC sí llegue a la tarea abierta, que es
    justo lo que nadie se entera de mirar.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())

from datetime import date, timedelta

from quipux import almacen

fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


BD = os.path.join(tempfile.mkdtemp(prefix='quipux_'), 'prueba.db')
HOY = date.today()
AYER = (HOY - timedelta(days=4)).isoformat()
PRONTO = (HOY + timedelta(days=2)).isoformat()


def doc(id_, numero, asunto, plazo=None, seguro=True, estado='abierto',
        area='PLANIFICACIÓN', bandeja='Recibidos', adjuntos=0):
    return {'id': id_, 'numero': numero, 'asunto': asunto, 'de': 'Quien sea',
            'tipo': 'Oficio', 'fecha_doc': '2026-09-01', 'area': area,
            'bandeja': bandeja, 'estado': estado, 'n_adjuntos': adjuntos,
            'enlace': 'https://dq.cuenca.gob.ec/x', 'carpeta': '/tmp/x',
            'plazo': {'fecha': plazo or '', 'origen': 'trámite' if seguro else 'texto',
                      'seguro': seguro}}


LOTE = [
    doc('101', 'DGPG-2050-2026', 'Matriz de requerimientos', AYER, True, adjuntos=2),
    doc('102', 'CSC-MEMO-0205', 'Solicitud de equipo', PRONTO, False,
        area='OBSERVATORIO', bandeja='Reasignados'),
    doc('103', 'CSC-MEMO-0196', 'Curso virtual', None, estado='cerrado',
        bandeja='Archivados', adjuntos=1),
]

print('\n-- Guardar lo recogido --')
check('entran los tres', almacen.guardar(LOTE, BD), 3)
check('la segunda pasada no duplica',
      (almacen.guardar(LOTE, BD), len(almacen.documentos(BD, ver='todos'))), (3, 3))

r = almacen.resumen(BD)
print('\n-- El marcador --')
check('total', r['total'], 3)
check('abiertos: lo archivado no cuenta', r['abiertos'], 2)
check('vencidos', r['vencidos'], 1)
check('con plazo', r['con_plazo'], 2)
check('deducidos: se distinguen de los que dio el sistema', r['deducidos'], 1)
check('adjuntos', r['adjuntos'], 3)
check('las dos áreas', sorted(r['areas']), ['OBSERVATORIO', 'PLANIFICACIÓN'])

print('\n-- Filtrar --')
check('sólo los vencidos',
      [d['numero'] for d in almacen.documentos(BD, ver='vencidos')], ['DGPG-2050-2026'])
check('por área',
      [d['numero'] for d in almacen.documentos(BD, ver='todos', area='OBSERVATORIO')],
      ['CSC-MEMO-0205'])
check('buscando por número',
      [d['numero'] for d in almacen.documentos(BD, ver='todos', busca='0205')],
      ['CSC-MEMO-0205'])
check('buscando por asunto',
      [d['numero'] for d in almacen.documentos(BD, ver='todos', busca='matriz')],
      ['DGPG-2050-2026'])
check('lo que vence antes va primero',
      [d['numero'] for d in almacen.documentos(BD, ver='con_plazo')],
      ['DGPG-2050-2026', 'CSC-MEMO-0205'])


print('\n-- Las tareas --')
creadas, actualizadas = almacen.crear_tareas(LOTE, BD)
check('una tarea por documento con plazo', (creadas, actualizadas), (2, 0))
check('lo archivado sin plazo no genera tarea', len(almacen.tareas(BD)), 2)
check('y salen por orden de vencimiento',
      [t['vence'] for t in almacen.tareas(BD)], [AYER, PRONTO])
check('se sabe cuál es deducida',
      [t['seguro'] for t in almacen.tareas(BD)], [1, 0])

creadas, actualizadas = almacen.crear_tareas(LOTE, BD)
check('la segunda pasada no vuelve a crearlas', (creadas, actualizadas), (0, 0))

print('\n-- El plazo cambia en CuencaDOC --')
NUEVO = (HOY + timedelta(days=20)).isoformat()
movido = [doc('101', 'DGPG-2050-2026', 'Matriz de requerimientos', NUEVO, True)]
creadas, actualizadas = almacen.crear_tareas(movido, BD)
check('la tarea abierta se pone al día', (creadas, actualizadas), (0, 1))
check('con la fecha nueva',
      [t['vence'] for t in almacen.tareas(BD) if '2050' in t['titulo']], [NUEVO])

print('\n-- Lo que ya cerró una persona se respeta --')
tarea = [t for t in almacen.tareas(BD) if '2050' in t['titulo']][0]
almacen.marcar_tarea(tarea['id'], 'hecha', BD)
check('deja de estar pendiente',
      any('2050' in t['titulo'] for t in almacen.tareas(BD)), False)
check('pero sigue existiendo',
      any('2050' in t['titulo'] for t in almacen.tareas(BD, estado='hecha')), True)

otra_vez = [doc('101', 'DGPG-2050-2026', 'Matriz de requerimientos', AYER, True)]
creadas, actualizadas = almacen.crear_tareas(otra_vez, BD)
check('el Municipio no la reabre cambiando la fecha', (creadas, actualizadas), (0, 0))
check('sigue cerrada',
      any('2050' in t['titulo'] for t in almacen.tareas(BD)), False)

print('\n-- El registro de las pasadas --')
almacen.apuntar_pasada({'documentos': 3, 'nuevos': 3, 'adjuntos': 3,
                        'segundos': 12, 'fallos': ['una bandeja no contestó']}, BD)
r = almacen.resumen(BD)
check('queda constancia de cuándo se trajo', bool(r['ultima_pasada']), True)
check('y de lo que falló', 'no contestó' in r['ultimos_fallos'], True)
check('se sabe si es de hoy', r['al_dia'], True)

print('\n-- Una base que no existía --')
nueva = os.path.join(tempfile.mkdtemp(prefix='quipux2_'), 'sub', 'otra.db')
check('se crea sola, sin migraciones que aplicar',
      (almacen.resumen(nueva)['total'], os.path.exists(nueva)), (0, True))


# ============================================================
#  Lo que dice el TEXTO del documento
# ============================================================
print('\n-- Los compromisos que salen de leer el documento --')
COMPROMISOS = [
    {'que': 'Remitir la matriz de requerimientos ciudadanos priorizados actualizada',
     'entregable': 'Matriz en Excel', 'para_cuando': PRONTO,
     'a_quien': 'Dirección de Gestión de Planificación',
     'cita': 'solicito remitir hasta el 8 de septiembre la matriz actualizada',
     'es_para_mi': True, 'urgente': True},
    {'que': 'Designar un delegado para la mesa técnica',
     'entregable': '', 'para_cuando': '', 'a_quien': 'Secretaría',
     'cita': 'sírvase designar un delegado', 'es_para_mi': True, 'urgente': False},
    # Éste NO es suyo: se descarta al guardar.
    {'que': 'Archivar el expediente', 'entregable': '', 'para_cuando': '',
     'a_quien': '', 'cita': 'para conocimiento de la unidad',
     'es_para_mi': False, 'urgente': False},
]

check('no se ha leído todavía', almacen.ya_leido('101', BD), False)
check('se guardan sólo los que le tocan a él',
      almacen.guardar_compromisos('101', COMPROMISOS, None, BD), 2)
check('y queda constancia de que ya se leyó', almacen.ya_leido('101', BD), True)

lista = almacen.compromisos(BD)
check('salen los dos', len(lista), 2)
check('el que tiene fecha va primero', lista[0]['para_cuando'], PRONTO)
check('con la frase textual que lo respalda',
      'hasta el 8 de septiembre' in (lista[0]['cita'] or ''), True)
check('y con el documento del que sale', lista[0]['numero'], 'DGPG-2050-2026')
check('se sabe qué hay que entregar', lista[0]['entregable'], 'Matriz en Excel')

check('leer otra vez no duplica',
      (almacen.guardar_compromisos('101', COMPROMISOS, None, BD),
       len(almacen.compromisos(BD))), (0, 2))

almacen.marcar_compromiso(lista[0]['id'], 'hecho', BD)
check('marcado como hecho, sale de la lista', len(almacen.compromisos(BD)), 1)
check('pero no se pierde', len(almacen.compromisos(BD, estado='hecho')), 1)
check('y volver a leer el documento no lo resucita',
      (almacen.guardar_compromisos('101', COMPROMISOS, None, BD),
       len(almacen.compromisos(BD))), (0, 1))

print('\n-- La sesión que permite sincronizar sin molestar a nadie --')
check('al principio no hay ninguna', almacen.leer_sesion(BD)[0], None)
almacen.guardar_sesion({'PHPSESSID': 'abc123', 'EDOCID': 'xyz'}, BD)
guardada, cuando = almacen.leer_sesion(BD)
check('se guarda', guardada, {'PHPSESSID': 'abc123', 'EDOCID': 'xyz'})
check('con la hora en que se abrió', bool(cuando), True)
almacen.olvidar_sesion(BD)
check('y se puede olvidar cuando caduca', almacen.leer_sesion(BD)[0], None)

print('\n' + ('TODO CORRECTO' if not fallos else
              '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
