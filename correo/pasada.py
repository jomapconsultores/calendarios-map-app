# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Una pasada: las nueve cuentas, lo nuevo de cada una, y sus vínculos.

Las cuentas se leen una detrás de otra y no a la vez. Son nueve conexiones
cortas; en paralelo se ganarían unos segundos a cambio de nueve hilos
escribiendo en el mismo SQLite. Y si una cuenta falla —la contraseña caducó, el
token de Microsoft hay que renovarlo—, se dice cuál y se sigue con las demás.
"""
import time

from . import almacen, conector, lectura, relacion


def ejecutar(cuenta=None, dias=lectura.DIAS_PRIMERA_VEZ, ruta=None, registro=print):
    inicio = time.time()
    cuentas = conector.elegir(cuenta)
    propias = [c['direccion'] for c in conector.cuentas()]
    conocidos = relacion.quipux_conocidos(registro)
    resumen = {'nuevos': 0, 'copias': 0, 'por_cuenta': {}, 'fallos': [],
               'quipux_conocidos': len(conocidos)}

    with almacen.abrir(ruta) as db:
        for c in cuentas:
            direccion = c['direccion']
            cuenta_res = {'nuevos': 0, 'leidos': 0, 'carpetas': []}
            resumen['por_cuenta'][direccion] = cuenta_res
            registro(f'[correo] {direccion}…')

            def alguardar(mensaje, uid, carpeta):
                mid, nuevo = almacen.guardar(db, mensaje, direccion, carpeta, uid)
                if nuevo:
                    almacen.guardar_vinculos(db, mid, relacion.vinculos(
                        mensaje, conocidos, propias))
                    cuenta_res['nuevos'] += 1
                    resumen['nuevos'] += 1
                resumen['copias'] += 1

            try:
                con = conector.conectar(c)
            except conector.ErrorCorreo as e:
                resumen['fallos'].append(str(e))
                cuenta_res['error'] = str(e)
                continue
            try:
                for carpeta in conector.carpetas_a_leer(con, c):
                    cuenta_res['carpetas'].append(carpeta.strip('"'))
                    try:
                        cuenta_res['leidos'] += lectura.leer_carpeta(
                            con, db, direccion, carpeta,
                            lambda m, uid, _c=carpeta: alguardar(m, uid, _c), dias)
                    except Exception as e:
                        resumen['fallos'].append(f'{direccion} {carpeta}: {str(e)[:150]}')
            finally:
                try:
                    con.logout()
                except Exception:
                    pass

        resumen['segundos'] = int(time.time() - inicio)
        almacen.apuntar_pasada(db, resumen)
    return resumen


def relacionar_de_nuevo(ruta=None, registro=print):
    """Vuelve a calcular los vínculos con el Quipux y los plazos de todo lo
    guardado. Sirve después de recoger quipux nuevos: un correo de hace un mes
    que citaba un número desconocido pasa a estar comprobado."""
    conocidos = relacion.quipux_conocidos(registro)
    propias = [c['direccion'] for c in conector.cuentas()]
    tipos = ('quipux', 'plazo', 'plataforma')
    cambiados = 0
    with almacen.abrir(ruta) as db:
        for m in almacen.todos_para_relacionar(ruta):
            nuevos = relacion.vinculos(m, conocidos, propias)
            almacen.guardar_vinculos(db, m['id'], nuevos, tipos=tipos)
            cambiados += 1
    return {'mensajes': cambiados, 'quipux_conocidos': len(conocidos)}
