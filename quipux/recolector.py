# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""La pasada completa: entrar, recorrer, bajar, ordenar y avisar.

El orden importa y no es casual:

  1. Se recorren TODAS las áreas antes de bajar nada. Así, si el servidor del
     municipio se cae a mitad de la descarga, al menos queda el índice de lo que
     hay —que es lo que urge un lunes— aunque falten archivos.
  2. Se baja sólo lo que no está. Lo que ya se descargó y no ha cambiado se
     salta; una pasada diaria sobre doscientos documentos no puede volver a
     bajar doscientos adjuntos cada día contra un servidor público.
  3. Los errores no detienen la pasada. Un documento que no abre, una bandeja
     que no responde: se apunta, se sigue, y al final se dice qué falló. Un
     fallo que interrumpe todo convierte un problema pequeño en no tener nada.

Lo que este módulo NO hace, y conviene que siga siendo así: no firma, no
reasigna, no comenta y no borra. Sólo mira y descarga. Es la cuenta de una
persona en un sistema del municipio, y lo único que debe pasar ahí es lo que
esa persona haría a mano.
"""
import os
import re
import traceback
from datetime import datetime

from . import almacen, archivo, documentos as docs, lectura, planificacion
from .sesion import ErrorQuipux, Quipux

# Dónde queda todo. Se puede cambiar con QUIPUX_DESTINO en el entorno.
DESTINO_POR_DEFECTO = os.path.join(
    os.path.expanduser('~'), 'Documentos', 'Quipux')

# Bandejas que se recorren, por el nombre con que las llama el propio menú.
# Vacío = todas las que haya.
BANDEJAS_POR_DEFECTO = ()

ARCHIVO_ESTADO = '_estado.json'


class Recolector:
    def __init__(self, destino=None, bandejas=BANDEJAS_POR_DEFECTO,
                 registro=print, limite=None, con_texto=True, leer_texto=True):
        self.destino = destino or os.getenv('QUIPUX_DESTINO') or DESTINO_POR_DEFECTO
        self.bandejas = tuple(b.lower() for b in (bandejas or ()))
        self.log = registro
        self.limite = limite            # tope de documentos, para probar sin bajarlo todo
        self.con_texto = con_texto
        # Leer el documento con IA para saber qué hay que entregar. Se puede
        # apagar: sin clave de IA no pasa nada grave —el plazo que da el propio
        # sistema se sigue leyendo—, lo que se pierde es el detalle.
        self.leer_texto = leer_texto and lectura.disponible()
        self.q = None
        self.documentos = []
        self.fallos = []

    # ------------------------------------------------------------------
    def ejecutar(self, volcar_cronograma=False, entrar_si_hace_falta=True):
        os.makedirs(self.destino, exist_ok=True)
        estado = archivo.leer_estado(os.path.join(self.destino, ARCHIVO_ESTADO))
        inicio = datetime.now()

        self.q = Quipux(registro=self.log)
        # Si hay una sesión guardada y sigue valiendo, se aprovecha: entrar de
        # nuevo echaría la que hay (CuencaDOC admite una sola) y, cuando salta
        # el control de la imagen, exigiría a una persona. La sincronización
        # continua vive de esto.
        galletas, _ = almacen.leer_sesion()
        if galletas:
            self.q.poner_galletas(galletas)
            if self.q.sigue_dentro():
                self.log('[quipux] se aprovecha la sesión ya abierta')
            else:
                almacen.olvidar_sesion()
                if not entrar_si_hace_falta:
                    return {'sin_sesion': True, 'documentos': 0, 'nuevos': 0,
                            'adjuntos': 0, 'con_plazo': 0, 'fallos': [],
                            'indices': {}, 'destino': self.destino, 'segundos': 0}
                self.q = Quipux(registro=self.log)
                self.q.entrar()
                almacen.guardar_sesion(self.q.galletas())
        else:
            if not entrar_si_hace_falta:
                return {'sin_sesion': True, 'documentos': 0, 'nuevos': 0,
                        'adjuntos': 0, 'con_plazo': 0, 'fallos': [],
                        'indices': {}, 'destino': self.destino, 'segundos': 0}
            self.q.entrar()
            almacen.guardar_sesion(self.q.galletas())

        # La sesión NO se cierra: se deja abierta para la próxima pasada.
        # Cerrarla obligaría a entrar otra vez dentro de un cuarto de hora y,
        # si el sistema levanta el control de la imagen, a molestar a alguien
        # para algo que ya estaba resuelto.
        self._recorrer_areas(estado)
        almacen.guardar_sesion(self.q.galletas())

        indices = self._escribir_indices()
        resumen = {
            'documentos': len(self.documentos),
            'con_plazo': sum(1 for d in self.documentos if (d.get('plazo') or {}).get('fecha')),
            'nuevos': sum(1 for d in self.documentos if d.get('nuevo')),
            'adjuntos': sum(d.get('n_adjuntos', 0) for d in self.documentos),
            'fallos': self.fallos,
            'indices': indices,
            'destino': self.destino,
            'segundos': round((datetime.now() - inicio).total_seconds()),
        }

        # Todo se guarda AQUÍ, en el propio servidor: un archivo SQLite junto a
        # los documentos descargados. Sin servicios de por medio, sin claves que
        # configurar y sin migraciones que aplicar. Lo que se recogió se puede
        # mirar aunque no haya internet, que es justo cuando más falta hace
        # saber qué se debía para hoy.
        almacen.guardar(self.documentos)
        creadas, actualizadas = almacen.crear_tareas(self.documentos)
        resumen['tareas'] = {'creadas': creadas, 'actualizadas': actualizadas}
        almacen.apuntar_pasada(resumen)

        # A la plataforma en la nube sólo se sube si alguien lo pide
        # expresamente. Ya no es el camino: es un extra.
        if volcar_cronograma:
            db = planificacion.cliente_de_la_plataforma(self.log)
            if db is not None:
                resumen['publicado'] = planificacion.publicar(db, self.documentos,
                                                              registro=self.log)
                resumen['cronograma'] = planificacion.volcar(db, self.documentos,
                                                             registro=self.log)
        return resumen

    # ------------------------------------------------------------------
    def _recorrer_areas(self, estado):
        perfiles = [p for p in self.q.perfiles() if p['institucional']]
        if not perfiles:
            raise ErrorQuipux('La cuenta no tiene ningún área institucional.')
        self.log(f'[quipux] {len(perfiles)} área(s): '
                 + ', '.join(p['area'] for p in perfiles))

        for perfil in perfiles:
            if not perfil['activo'] and not self.q.cambiar_perfil(perfil['id']):
                self.fallos.append(f"no se pudo entrar al área {perfil['area']}")
                self.log(f"[quipux] AVISO: no se pudo cambiar al área {perfil['area']}; "
                         'se salta y se sigue con las demás')
                continue
            self.log(f"[quipux] área: {perfil['area']}")
            try:
                bandejas = self.q.bandejas()
            except Exception as e:
                self.fallos.append(f"{perfil['area']}: no se pudo leer el menú ({e})")
                continue
            for bandeja in bandejas:
                if self.bandejas and bandeja['nombre'].lower() not in self.bandejas:
                    continue
                try:
                    self._recorrer_bandeja(perfil, bandeja, estado)
                except Exception as e:
                    self.fallos.append(f"{perfil['area']} / {bandeja['nombre']}: {str(e)[:120]}")
                    self.log(f"[quipux] fallo en {bandeja['nombre']}: {str(e)[:150]}")

    def _recorrer_bandeja(self, perfil, bandeja, estado):
        r = self.q.get(bandeja['ruta'])
        if r.status_code != 200:
            raise ErrorQuipux(f'HTTP {r.status_code}')
        base = self.q._url(bandeja['ruta'])
        filas = docs.leer_bandeja(r.text, base)

        # Las demás páginas. Se piden por su enlace, no adivinando el número:
        # cada bandeja pagina a su manera y adivinar acaba en páginas vacías
        # que parecen el final de la lista.
        vistas, pendientes = set(), docs.enlaces_de_paginas(r.text, base)
        while pendientes:
            pagina = pendientes.pop(0)
            if pagina['destino'] in vistas:
                continue
            vistas.add(pagina['destino'])
            try:
                ruta = pagina['destino']
                if not ruta.lower().startswith(('http', 'cuerpo', '/')):
                    ruta = 'cuerpo.php?' + ruta.lstrip('?&')
                rp = self.q.get(ruta)
                if rp.status_code != 200:
                    continue
                nuevas = docs.leer_bandeja(rp.text, base)
                conocidos = {f['id'] for f in filas}
                filas += [f for f in nuevas if f['id'] not in conocidos]
                for otra in docs.enlaces_de_paginas(rp.text, base):
                    if otra['destino'] not in vistas:
                        pendientes.append(otra)
            except Exception:
                continue

        esperados = bandeja.get('esperados')
        if esperados is not None and len(filas) < esperados:
            # Se dice, no se calla. Que el menú anuncie 107 y aquí lleguen 20 es
            # exactamente el fallo que hay que ver: la pasada «funcionó» y se
            # dejó fuera ochenta y siete compromisos.
            aviso = (f"{bandeja['nombre']}: el menú anuncia {esperados} documentos "
                     f'y se leyeron {len(filas)}')
            self.fallos.append(aviso)
            self.log(f'[quipux] AVISO — {aviso}')

        self.log(f"[quipux]   {bandeja['nombre']}: {len(filas)} documento(s)")
        for fila in filas:
            if self.limite and len(self.documentos) >= self.limite:
                return
            try:
                self._procesar(perfil, bandeja, fila, estado)
            except Exception as e:
                self.fallos.append(f"{fila.get('numero', fila.get('id'))}: {str(e)[:120]}")

    # ------------------------------------------------------------------
    def _procesar(self, perfil, bandeja, fila, estado):
        registro = dict(fila)
        registro['area'] = perfil['area']
        registro['bandeja'] = bandeja['nombre']
        carpeta = archivo.carpeta_del_documento(
            self.destino, perfil['area'], registro, bandeja['nombre'])
        registro['carpeta'] = carpeta
        registro['enlace'] = archivo.enlace_al_documento(
            self.q.base, registro.get('id', ''), registro.get('numero', ''))

        previo = estado.get(registro.get('id', ''))
        sin_cambios = (previo and previo.get('ultima') == registro.get('ultima')
                       and os.path.isdir(carpeta))
        registro['nuevo'] = not previo

        texto, anexos = '', []
        if not sin_cambios:
            ficha = self._abrir_ficha(registro)
            texto = ficha.get('texto', '') if self.con_texto else ''
            anexos = ficha.get('anexos', [])

        plazo = docs.deducir_plazo(registro, texto)
        registro['plazo'] = {'fecha': plazo[0].isoformat() if plazo[0] else '',
                             'origen': plazo[1], 'seguro': plazo[2]}
        registro['estado'] = 'cerrado' if bandeja['nombre'].lower().startswith(
            ('archivad', 'eliminad')) else 'abierto'

        if sin_cambios:
            registro['n_adjuntos'] = (previo or {}).get('adjuntos', 0)
            self.documentos.append(registro)
            return

        os.makedirs(carpeta, exist_ok=True)
        archivo.escribir_ficha(carpeta, registro, plazo, self.q.base, texto)
        registro['n_adjuntos'] = self._bajar_anexos(carpeta, anexos, registro)
        self._leer_lo_que_pide(registro, texto)
        self.documentos.append(registro)

    def _leer_lo_que_pide(self, registro, texto):
        """Lee el texto del documento para saber QUÉ hay que entregar.

        La bandeja dice el asunto y a veces una fecha; el asunto no dice qué
        hacer. «ACTUALIZACIÓN MATRIZ DE REQUERIMIENTOS, CORTE AGOSTO» no aclara
        si hay que llenarla, revisarla o remitirla, ni a quién, ni en qué
        formato. Eso está en el párrafo de adentro.

        Sólo se lee UNA VEZ por documento: son doscientos y pico, y volver a
        mandarlos al modelo en cada pasada sería pagar todos los días por el
        mismo resultado."""
        if not texto or not self.leer_texto:
            return
        try:
            if almacen.ya_leido(registro['id']):
                return
            compromisos, aviso = lectura.leer_compromisos(registro, texto)
            almacen.guardar_compromisos(registro['id'], compromisos, aviso)
            if compromisos:
                registro['compromisos'] = len(compromisos)
                self.log(f"[quipux]   {registro.get('numero')}: "
                         f'{len(compromisos)} cosa(s) que entregar')
        except Exception as e:
            # Que la IA falle no puede costar el documento: ya está descargado,
            # clasificado y con su plazo del sistema. Lo que se pierde es el
            # detalle, y se apunta para poder reintentarlo.
            self.fallos.append(f"{registro.get('numero')}: no se pudo leer ({str(e)[:90]})")

    def _abrir_ficha(self, registro):
        """La pantalla del documento. Se prueban las rutas que usa Quipux para
        abrirlo; con la primera que traiga algo reconocible, basta."""
        id_doc, numero = registro.get('id', ''), registro.get('numero', '')
        intentos = [
            f'ver_documento.php?id_documento={id_doc}&nro_documento={numero}',
            f'documento/ver_documento.php?id_documento={id_doc}',
            f'ver_docum.php?id_documento={id_doc}',
            f'documento/documento_ver.php?id_documento={id_doc}',
        ]
        for ruta in intentos:
            try:
                r = self.q.get(ruta)
            except Exception:
                continue
            if r.status_code != 200 or len(r.text) < 400:
                continue
            if 'form_login' in r.text and 'public_key' in r.text:
                raise ErrorQuipux('la sesión de CuencaDOC se cerró a mitad de la pasada')
            ficha = docs.leer_ficha(r.text, self.q._url(ruta))
            if ficha['texto'] or ficha['anexos']:
                return ficha
        return {'texto': '', 'anexos': []}

    def _bajar_anexos(self, carpeta, anexos, registro):
        bajados = 0
        for i, anexo in enumerate(anexos, start=1):
            nombre = archivo.nombre_de_anexo(i, anexo['nombre'], anexo['url'])
            ruta = os.path.join(carpeta, nombre)
            if os.path.exists(ruta) and os.path.getsize(ruta) > 0:
                bajados += 1
                continue
            try:
                r = self.q.s.get(anexo['url'], timeout=90, stream=True)
                if r.status_code != 200:
                    self.fallos.append(f"{registro.get('numero')}: {nombre} HTTP {r.status_code}")
                    continue
                tipo = (r.headers.get('Content-Type') or '').lower()
                # Un HTML donde debería haber un PDF es la página de sesión
                # caducada. Guardarlo dejaría un archivo con el nombre correcto
                # y la basura dentro, que es peor que no tenerlo.
                if 'text/html' in tipo and not nombre.lower().endswith(('.htm', '.html')):
                    self.fallos.append(f"{registro.get('numero')}: {nombre} llegó como página web")
                    continue
                with open(ruta, 'wb') as f:
                    for trozo in r.iter_content(65536):
                        if trozo:
                            f.write(trozo)
                if os.path.getsize(ruta) == 0:
                    os.remove(ruta)
                    continue
                bajados += 1
            except Exception as e:
                self.fallos.append(f"{registro.get('numero')}: {nombre} — {str(e)[:90]}")
        return bajados

    # ------------------------------------------------------------------
    def _escribir_indices(self):
        salida = {}
        try:
            salida['excel'] = archivo.escribir_indice_excel(
                os.path.join(self.destino, 'INDICE.xlsx'), self.documentos)
        except Exception as e:
            self.log(f'[quipux] no se pudo escribir el Excel: {str(e)[:120]}')
        try:
            salida['html'] = archivo.escribir_indice_html(
                os.path.join(self.destino, 'INDICE.html'), self.documentos, '')
        except Exception as e:
            self.log(f'[quipux] no se pudo escribir el índice HTML: {str(e)[:120]}')
        try:
            # El que lee la pantalla de la plataforma.
            salida['json'] = archivo.escribir_indice_json(
                os.path.join(self.destino, 'INDICE.json'), self.documentos)
        except Exception as e:
            self.log(f'[quipux] no se pudo escribir el índice para la web: {str(e)[:120]}')
        try:
            archivo.escribir_estado(
                os.path.join(self.destino, ARCHIVO_ESTADO), self.documentos)
        except Exception:
            pass
        return salida


def arrancar_autosync(app=None, interval_min=30, registro=print):
    """Mantiene la agenda al día sola, mientras la sesión aguante.

    La sesión de CuencaDOC se guarda y se reutiliza: mientras el sistema la dé
    por buena, cada pasada entra sin pedir nada a nadie. Cuando caduque —y
    caducará— la siguiente intentará entrar de nuevo; si el sistema levanta el
    control de la imagen, se para y lo dice en la pantalla, que es donde alguien
    puede resolverlo en cinco segundos. Lo que NO hace es reintentar el acceso
    en bucle: eso bloquearía la cuenta.

    Media hora, no cinco minutos: son dos áreas, siete bandejas y un servidor
    público del municipio. Lo que cambia en la bandeja de un coordinador entre
    las 9:00 y las 9:30 casi nunca es algo que se resuelva antes de las 9:30."""
    try:
        import fcntl
    except Exception:
        registro('[quipux] fcntl no disponible: la sincronización automática '
                 'queda desactivada (usa el botón de la pantalla)')
        return
    try:
        import tempfile
        ruta = os.path.join(tempfile.gettempdir(), 'quipux_autosync.lock')
        archivo_lock = open(ruta, 'w')
        fcntl.flock(archivo_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if app is not None:
            app._quipux_lock = archivo_lock
    except Exception:
        return                      # otro worker ya se lo quedó

    import threading
    import time as _time

    def _bucle():
        _time.sleep(240)            # que termine de levantar el despliegue
        while True:
            try:
                galletas, _ = almacen.leer_sesion()
                if not galletas:
                    registro('[quipux] sin sesión guardada: esperando a que '
                             'alguien entre desde la pantalla')
                else:
                    r = ejecutar(registro=registro, entrar_si_hace_falta=False)
                    if r.get('sin_sesion'):
                        registro('[quipux] la sesión de CuencaDOC caducó; '
                                 'hay que volver a entrar desde /quipux')
            except Exception as e:
                registro(f'[quipux] sincronización: {str(e)[:200]}')
            _time.sleep(interval_min * 60)

    threading.Thread(target=_bucle, name='quipux-sync', daemon=True).start()
    registro(f'[quipux] sincronización automática activa (cada {interval_min} min)')


def ejecutar(destino=None, bandejas=(), limite=None, volcar=False, registro=print,
             entrar_si_hace_falta=True):
    """Punto de entrada. Devuelve el resumen; no lanza salvo que no se pueda
    ni entrar, que es el único caso en el que no hay nada que hacer."""
    r = Recolector(destino=destino, bandejas=bandejas, limite=limite, registro=registro)
    try:
        return r.ejecutar(volcar_cronograma=volcar,
                          entrar_si_hace_falta=entrar_si_hace_falta)
    except ErrorQuipux:
        raise
    except Exception as e:
        registro('[quipux] fallo inesperado:\n' + traceback.format_exc()[-1200:])
        raise ErrorQuipux(str(e)[:200])
