# -*- coding: utf-8 -*-
"""Prueba del lector de CuencaDOC, sin tocar el servidor del Municipio.

Se le dan trozos de HTML como los que devuelve el sistema y se mira qué
entiende. Lo que se vigila es lo que rompe callando:

  * que la tabla cambie de orden de columnas y se acabe guardando el número de
    trámite en el campo del asunto;
  * que un plazo en «días término» se cuente como días corridos — casi una
    semana de diferencia, justo la que separa cumplir de incumplir;
  * que un asunto con comillas, dos puntos o barras de fecha reviente el nombre
    del archivo en Windows;
  * que se dé por buena una fecha deducida de una frase como si el sistema la
    hubiera confirmado.
"""
import sys, os
sys.path.insert(0, os.getcwd())

from datetime import date

from quipux import archivo
from quipux import documentos as docs
from quipux.sesion import Quipux

fallos = []


def check(titulo, obtenido, esperado):
    ok = obtenido == esperado
    print(('  OK  ' if ok else ' FALLA') + '  ' + titulo)
    if not ok:
        print('        esperado:', esperado)
        print('        obtenido:', obtenido)
        fallos.append(titulo)


BASE = 'https://dq.cuenca.gob.ec/'

# Réplica de la bandeja real: mismos encabezados y el mismo mostrar_documento().
BANDEJA = """
<html><body><table>
<tr><th></th><th>De</th><th>Asunto</th><th>Fecha Documento</th>
    <th>Número Documento</th><th>No. Referencia</th><th>Tipo Documento</th>
    <th>Nro. Trámite</th><th>Usuario Anterior</th><th>Categoría</th>
    <th>Fecha Ultima Acción</th></tr>
<tr>
  <td><img src="/img/anexo.gif" title="Anexos"></td>
  <td><a href='javascript:mostrar_documento("20260001491136975931","DGPG-2050-2026","2")'>Daniel Marcelo Garcia Pineda</a></td>
  <td><a href='javascript:mostrar_documento("20260001491136975931","DGPG-2050-2026","2")'>ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS CIUDADANOS</a></td>
  <td>2026-09-03 00:00:00</td><td>DGPG-2050-2026</td><td></td><td>Oficio</td>
  <td>IMC-2026-72882</td><td>Xavier Barrera Vidal</td><td>Normal</td>
  <td>2026-09-04 11:15:20</td>
</tr>
<tr>
  <td></td>
  <td><a href='javascript:mostrar_documento("20260002471136772111","CSC-MEMO-DE-2026-0196","2")'>Xavier Barrera Vidal</a></td>
  <td><a href='javascript:mostrar_documento("20260002471136772111","CSC-MEMO-DE-2026-0196","2")'>Curso virtual "MASCULINIDADES"</a></td>
  <td>2026-09-01 00:00:00</td><td>CSC-MEMO-DE-2026-0196</td><td></td><td>Memorando</td>
  <td>CSC-2026-1967</td><td>Xavier Barrera Vidal</td><td>Normal</td>
  <td>2026-09-01 11:30:49</td>
</tr>
</table></body></html>
"""

print('\n-- Leer la bandeja --')
filas = docs.leer_bandeja(BANDEJA, BASE)
check('encuentra los dos documentos', len(filas), 2)
uno = filas[0]
check('el identificador interno', uno['id'], '20260001491136975931')
check('el número oficial', uno['numero'], 'DGPG-2050-2026')
check('el asunto entero', uno['asunto'], 'ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS CIUDADANOS')
check('quién lo manda', uno['de'], 'Daniel Marcelo Garcia Pineda')
check('el tipo', uno['tipo'], 'Oficio')
check('el trámite', uno['tramite'], 'IMC-2026-72882')
check('la fecha del documento', uno['fecha_doc'], '2026-09-03 00:00:00')
check('se ve que lleva adjuntos', uno['tiene_anexos'], True)
check('y que el segundo no', filas[1]['tiene_anexos'], False)

print('\n-- La bandeja de Reasignados: la que trae el plazo de verdad --')
# Es la bandeja donde el sistema avisa de los vencidos, y su columna de plazo no
# se llama «vencimiento» sino «Fecha Max. de Respuesta». Si ese nombre no se
# reconoce, el plazo bueno se pierde y todo pasa por «deducido del texto»: la
# lista sale igual, con la mitad de la confianza que merece y sin que se note.
REASIGNADOS = """
<html><body><table>
<tr><th></th><th>Fecha Documento</th><th>Reasignado a</th><th>Comentario</th>
    <th>Fecha Reasignación</th><th>Fecha Max. de Respuesta</th><th>De</th>
    <th>Para</th><th>Asunto</th><th>Número Documento</th><th>Tipo Documento</th>
    <th>Nro. Trámite</th><th>Estado</th></tr>
<tr>
  <td><img src="/img/anexo.gif"></td>
  <td>2026-09-04 00:00:00</td><td>Xavier Barrera Vidal</td>
  <td>Estimado señor Director remito la informacion solicitada</td>
  <td>2026-09-04 16:05:50</td><td>2026-09-08</td>
  <td>Xavier Barrera Vidal</td><td>Daniel Marcelo Garcia Pineda</td>
  <td><a href='javascript:mostrar_documento("20260009991136999991","CSC-GPP-2026-116-TEMP","6")'>SOLICITUD DE INFORMACION DE INVERSIONES</a></td>
  <td>CSC-GPP-2026-116-TEMP</td><td>Oficio</td><td>IMC-2026-72348</td>
  <td>En Tramite</td>
</tr>
</table></body></html>
"""
rea = docs.leer_bandeja(REASIGNADOS, BASE)
check('se lee la fila de Reasignados', len(rea), 1)
check('con el plazo que da el sistema', rea[0].get('vence'), '2026-09-08')
check('y con quien la reasigno', rea[0].get('reasignado'), 'Xavier Barrera Vidal')
check('y la instruccion que traia',
      'remito la informacion' in (rea[0].get('comentario') or ''), True)
_f, _o, _seguro = docs.deducir_plazo(rea[0])
check('el plazo sale del sistema, no del texto', _f, date(2026, 9, 8))
check('y por eso cuenta como confirmado', _seguro, True)


print('\n-- La tabla cambia de orden --')
# El mismo contenido con las columnas movidas. Si se leyera por posición, el
# asunto vendría a parar al campo del tipo y nadie se enteraría.
REORDENADA = BANDEJA.replace(
    '<th>Asunto</th><th>Fecha Documento</th>', '<th>Fecha Documento</th><th>Asunto</th>'
).replace(
    """<td><a href='javascript:mostrar_documento("20260001491136975931","DGPG-2050-2026","2")'>ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS CIUDADANOS</a></td>
  <td>2026-09-03 00:00:00</td>""",
    """<td>2026-09-03 00:00:00</td>
  <td><a href='javascript:mostrar_documento("20260001491136975931","DGPG-2050-2026","2")'>ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS CIUDADANOS</a></td>""")
movida = docs.leer_bandeja(REORDENADA, BASE)
check('el asunto sigue siendo el asunto',
      movida[0]['asunto'], 'ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS CIUDADANOS')
check('y la fecha sigue siendo la fecha', movida[0]['fecha_doc'], '2026-09-03 00:00:00')

print('\n-- Una tabla que no es la bandeja --')
check('no se inventa documentos donde no los hay',
      docs.leer_bandeja('<table><tr><th>Hola</th></tr><tr><td>x</td></tr></table>', BASE), [])


# ------------------------------------------------------------------ los plazos
print('\n-- El plazo que da el sistema --')
f, origen, seguro = docs.deducir_plazo({'vence': '2026-10-15', 'asunto': 'x'})
check('se toma tal cual', f, date(2026, 10, 15))
check('y se marca como confirmado', seguro, True)

print('\n-- El plazo escrito en el documento --')
f, origen, seguro = docs.deducir_plazo(
    {'fecha_doc': '2026-09-03', 'asunto': 'Informe'},
    'Se solicita remitir la información hasta el 15 de octubre de 2026.')
check('se entiende la fecha en palabras', f, date(2026, 10, 15))
check('pero NO se da por confirmada', seguro, False)

f, _, _ = docs.deducir_plazo({'fecha_doc': '2026-09-03', 'asunto': ''},
                             'Remitir hasta el 20/09/2026 lo solicitado.')
check('también en dd/mm/aaaa', f, date(2026, 9, 20))

print('\n-- Días hábiles frente a días corridos --')
# 2026-09-03 es jueves. Cinco días hábiles llegan al jueves siguiente (10);
# cinco corridos, al martes 8. Contar unos por otros mueve el plazo dos días.
f, como, _ = docs.deducir_plazo(
    {'fecha_doc': '2026-09-03', 'asunto': ''},
    'Deberá informar en el término de 5 días hábiles.')
check('los hábiles saltan el fin de semana', f, date(2026, 9, 10))
check('y se dice cómo se contó', 'hábiles' in como, True)

f, como, _ = docs.deducir_plazo(
    {'fecha_doc': '2026-09-03', 'asunto': ''},
    'Deberá informar en el plazo de 5 días.')
check('los corridos se cuentan seguidos', f, date(2026, 9, 8))
check('y también se dice', 'corridos' in como, True)

print('\n-- Cuando no hay plazo --')
f, origen, seguro = docs.deducir_plazo({'fecha_doc': '2026-09-03', 'asunto': 'Saludo'},
                                       'Reciba un cordial saludo.')
check('no se inventa ninguno', (f, seguro), (None, False))


# ----------------------------------------------------------------- los anexos
FICHA = """
<html><body>
<p>Señor Coordinador: adjunto la matriz para su revisión.</p>
<a href="/anexos/descargar.php?id=99&nombre=matriz.xlsx">matriz.xlsx</a>
<a href="javascript:void(0);">Imprimir</a>
<a href="/bodega/2026/oficio-firmado.pdf">Oficio firmado</a>
<a href="/anexos/descargar.php?id=99&nombre=matriz.xlsx">matriz.xlsx</a>
</body></html>
"""
print('\n-- Los adjuntos de la ficha --')
ficha = docs.leer_ficha(FICHA, BASE)
check('encuentra los dos, sin repetir el mismo', len(ficha['anexos']), 2)
check('no confunde un botón con un archivo',
      any('void' in a['url'] for a in ficha['anexos']), False)
check('se queda con el texto del documento',
      'adjunto la matriz' in ficha['texto'], True)


# --------------------------------------------------------------- los nombres
print('\n-- Nombrar para poder encontrar --')
r = {'fecha_doc': '2026-09-03', 'tipo': 'Oficio', 'numero': 'DGPG-2050-2026',
     'asunto': 'ACTUALIZACIÓN MATRIZ: requerimientos 15/09 "prioritarios"'}
nombre = archivo.nombre_de_carpeta(r)
check('empieza por la fecha, para que ordene solo', nombre.startswith('2026-09-03'), True)
check('lleva el número con el que se cita el documento', 'DGPG-2050-2026' in nombre, True)
check('sin caracteres que Windows no admite',
      any(c in nombre for c in r'<>:"/\|?*'), False)
check('y sin pasarse de largo', len(nombre) <= 160, True)

check('un anexo conserva su extensión',
      archivo.nombre_de_anexo(3, 'Informe final.PDF', 'http://x/a.pdf'), '03_Informe-final.pdf')
check('y si no la trae, se saca de la dirección',
      archivo.nombre_de_anexo(1, 'descargar', 'http://x/bodega/doc.docx').endswith('.docx'), True)
check('un nombre reservado de Windows no rompe la carpeta',
      archivo.limpiar('AUX'), '_AUX')


# ----------------------------------------------------------------- el acceso
print('\n-- El cifrado de la contraseña --')
# Se comprueba contra una clave propia: que lo que se manda es exactamente
# «token|contraseña» cifrado con RSA, que es lo que el sistema espera.
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import serialization

privada = rsa.generate_private_key(public_exponent=65537, key_size=1024)
pem = privada.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo).decode()

apretada = pem.replace('\n', '')
check('la clave sin saltos de línea se reconstruye',
      'BEGIN PUBLIC KEY' in Quipux._pem(apretada) and
      len(Quipux._pem(apretada).strip().splitlines()) > 3, True)

publica = serialization.load_pem_public_key(Quipux._pem(apretada).encode())
cifrado = publica.encrypt(b'TOKEN123|mi-clave', padding.PKCS1v15())
check('y descifra lo que el sistema espera recibir',
      privada.decrypt(cifrado, padding.PKCS1v15()), b'TOKEN123|mi-clave')

print('\n' + ('TODO CORRECTO' if not fallos else
              '%d FALLO(S): %s' % (len(fallos), ', '.join(fallos))))
sys.exit(1 if fallos else 0)
