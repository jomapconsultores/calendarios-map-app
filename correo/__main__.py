# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Los comandos:

    python -m correo probar             las cuentas, y si se puede entrar a cada una
    python -m correo                    la pasada: lo nuevo de las nueve cuentas
    python -m correo traer <cuenta> [días]   lo mismo, de una sola cuenta
    python -m correo buscar <texto>     en todo lo guardado, de todas las cuentas
    python -m correo leer <n>           un mensaje entero (n = el #id del listado)
    python -m correo quipux <número>    los correos que hablan de ese documento
    python -m correo plazos             lo que tiene fecha de entrega por delante
    python -m correo citas              las invitaciones de calendario
    python -m correo relacionar         recalcula los vínculos con el Quipux
    python -m correo informe            escribe la página y la abre
    python -m correo pendientes         lo marcado y lo que llegó sin leer
    python -m correo publicar [--simular]   sube lo relacionado y lo pendiente a
                                        la plataforma; los plazos pasan al cronograma
    python -m correo diario             todo junto: traer, relacionar, publicar, página
    python -m correo instalar           deja la pasada programada (cada 15 minutos)
    python -m correo desinstalar        la quita

`relacionar` es para después de recoger quipux nuevos: un correo que citaba un
número que aún no estaba recogido pasa a estar comprobado.
"""
import sys
from datetime import date

from . import almacen, conector


def _consola_en_utf8():
    # Igual que en `quipux`: la consola de Windows va en cp1252 y una tilde
    # tumba el programa justo cuando iba a decir algo.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        except Exception:
            pass


def _probar():
    malas = 0
    for c in conector.cuentas():
        try:
            if c.get('via') == 'ews':
                from . import ews
                ews._token(c)
                carpetas = [f'{x} (por EWS)' for x in ews.CARPETAS]
                print(f"  OK     {c['direccion']:38} lee: {', '.join(carpetas)}")
                continue
            con = conector.conectar(c)
            carpetas = conector.carpetas_a_leer(con, c)
            con.logout()
            print(f"  OK     {c['direccion']:38} lee: {', '.join(x.strip(chr(34)) for x in carpetas)}")
        except conector.ErrorCorreo as e:
            malas += 1
            print(f"  FALLA  {e}")
    return 1 if malas else 0


def _linea(m):
    marcas = ' '.join(f"[{v['tipo']}:{v['clave']}]" if v['clave'] else f"[{v['tipo']}]"
                      for v in m['vinculos']
                      if v['tipo'] != 'plataforma')
    cuentas = ','.join(c.split('@')[0] for c in m['cuentas'])
    print(f"#{m['id']:<6} {(m['fecha'] or '')[:10]}  {(m['de'] or '')[:30]:30}  "
          f"{(m['asunto'] or '(sin asunto)')[:70]}")
    print(f"        {cuentas}  {marcas}".rstrip())


def _listar(mensajes, vacio='Nada.'):
    if not mensajes:
        print(vacio)
        return 0
    for m in mensajes:
        _linea(m)
    print(f'\n{len(mensajes)} mensaje(s)')
    return 0


def _leer(n):
    m = almacen.mensaje(n)
    if not m:
        print(f'No hay ningún mensaje #{n}.')
        return 1
    print(f"De:       {m['de']}\nPara:     {m['para']}\nFecha:    {m['fecha']}")
    print(f"Asunto:   {m['asunto']}")
    print('Está en:  ' + '; '.join(f"{d['cuenta']} ({d['carpeta'].strip(chr(34))})"
                                   for d in m['donde']))
    if m['adjuntos']:
        print('Adjuntos: ' + ', '.join(m['adjuntos']))
    for v in m['vinculos']:
        dudoso = '' if v['seguro'] else '  (sin comprobar)'
        print(f"Relación: {v['tipo']} {v['clave']} — {v['detalle'] or ''}{dudoso}")
    print('\n' + (m['texto'] or '(sin texto)'))
    return 0


def _pasada(cuenta=None, dias=None):
    from . import lectura, pasada
    r = pasada.ejecutar(cuenta, dias or lectura.DIAS_PRIMERA_VEZ)
    print('\n' + '=' * 60)
    for direccion, c in r['por_cuenta'].items():
        if c.get('error'):
            print(f'{direccion:38} NO SE PUDO — {c["error"]}')
        else:
            viejos = (f', {c["marcados_viejos"]} marcados de antes'
                      if c.get('marcados_viejos') else '')
            print(f'{direccion:38} {c["leidos"]:5} leídos, {c["nuevos"]:5} nuevos{viejos}')
    print('-' * 60)
    print(f"Nuevos           : {r['nuevos']}")
    print(f"Quipux conocidos : {r['quipux_conocidos']}")
    if r['fallos']:
        print(f"\nCon problemas ({len(r['fallos'])}):")
        for f in r['fallos'][:15]:
            print(f'  · {f}')
    print(f"Tardó            : {r['segundos']} s")
    res = almacen.resumen()
    print(f"Guardados        : {res['total']}  ·  relacionados: "
          + ', '.join(f'{k} {v}' for k, v in sorted(res['por_tipo'].items())))
    print('=' * 60)
    return 0


def _pendientes():
    from datetime import timedelta
    from . import publicar
    desde = (date.today() - timedelta(days=publicar.DIAS_SIN_LEER)).isoformat()
    mensajes = almacen.con_estado(desde, [c['direccion'] for c in conector.cuentas()])
    for m in mensajes:
        estado = (['★ marcado'] if m['marcado_en'] else []) +                  (['sin leer'] if m['sin_leer_en'] else []) +                  (['contestado'] if m['contestado'] else [])
        m['vinculos'] = [{'tipo': e, 'clave': ''} for e in estado] + m['vinculos']
    return _listar(mensajes, 'Nada marcado ni sin leer.')


def _publicar(simular=False):
    from . import publicar
    mensajes = publicar.para_publicar()
    tipos = {}
    for m in mensajes:
        for t in {v['tipo'] for v in m['vinculos']}:
            tipos[t] = tipos.get(t, 0) + 1
    print(f"A publicar   : {len(mensajes)}  ("
          + ', '.join(f'{k} {v}' for k, v in sorted(tipos.items())) + ')')
    # Aunque no haya nada se manda: la lista va completa, y una vacía es la
    # forma de decir que lo marcado de ayer ya se atendió.
    if simular:
        print('No se ha subido nada.')
        return 0
    cuentas = publicar.estado_cuentas()
    r = publicar.mandar(mensajes, cuentas)
    if not r.get('success'):
        print(f"No se pudo publicar: {r.get('error')}", file=sys.stderr)
        return 2
    for c in cuentas['cuentas']:
        if c['falla_desde']:
            print(f"NO ENTRA     : {c['cuenta']} desde {c['falla_desde'][:16]} — "
                  f"{(c['error'] or '')[:100]}")
    print(f"Publicados   : {r.get('publicados', 0)}"
          + (f"  ·  {r['atendidos']} ya atendido(s), retirado(s)" if r.get('atendidos') else ''))
    crono = r.get('cronograma') or {}
    if crono.get('error'):
        print(f"Cronograma   : no se pudo — {crono['error']}")
    else:
        print(f"Cronograma   : {crono.get('creadas', 0)} nueva(s), "
              f"{crono.get('actualizadas', 0)} con plazo cambiado, "
              f"{crono.get('respetadas', 0)} respetada(s) por estar cerradas")
    return 0


def _diario():
    """Lo que hace la tarea programada. Si publicar falla —sin internet, el
    servidor caído—, lo leído se queda guardado y se publica en la siguiente."""
    from . import informe, pasada
    _pasada()
    r = pasada.relacionar_de_nuevo()
    print(f"Relacionados de nuevo con {r['quipux_conocidos']} quipux conocidos.")
    try:
        _publicar()
    except RuntimeError as e:
        print(f'No se pudo publicar: {e}', file=sys.stderr)
    print(informe.escribir())
    return 0


def main(argv=None):
    _consola_en_utf8()
    argv = argv if argv is not None else sys.argv[1:]
    orden = (argv[0].lower() if argv else '')
    resto = argv[1:]
    try:
        if orden in ('probar', 'test'):
            return _probar()
        if orden == 'traer':
            cuenta = resto[0] if resto else None
            dias = int(resto[1]) if len(resto) > 1 else None
            return _pasada(cuenta, dias)
        if orden == 'buscar':
            return _listar(almacen.buscar(' '.join(resto), tope=40),
                           'Nada guardado coincide. ¿Se hizo ya una pasada?')
        if orden == 'leer' and resto:
            return _leer(resto[0].lstrip('#'))
        if orden == 'quipux' and resto:
            return _listar(almacen.vinculados('quipux', resto[0]),
                           f'Ningún correo habla de {resto[0]}.')
        if orden == 'plazos':
            return _listar(almacen.vinculados('plazo', desde=date.today().isoformat()),
                           'Ningún correo con fecha de entrega por delante.')
        if orden == 'pendientes':
            return _pendientes()
        if orden == 'citas':
            return _listar(almacen.vinculados('cita', tope=50), 'Ninguna invitación.')
        if orden == 'relacionar':
            from . import pasada
            r = pasada.relacionar_de_nuevo()
            print(f"{r['mensajes']} mensaje(s) revisados con "
                  f"{r['quipux_conocidos']} quipux conocidos.")
            return 0
        if orden == 'informe':
            import os
            from . import informe
            ruta = informe.escribir()
            print(ruta)
            if hasattr(os, 'startfile'):
                os.startfile(ruta)
            return 0
        if orden == 'publicar':
            return _publicar(simular='--simular' in resto)
        if orden == 'diario':
            return _diario()
        if orden == 'instalar':
            from .instalar import instalar
            return instalar()
        if orden == 'desinstalar':
            from .instalar import desinstalar
            return desinstalar()
        if orden in ('', 'pasada'):
            return _pasada()
        print(__doc__)
        return 2
    except (conector.ErrorCorreo, RuntimeError) as e:
        print(f'\nNo se pudo: {e}', file=sys.stderr)
        return 2
    except ModuleNotFoundError as e:
        print(f'\nFalta una librería ({e.name}). Se instala con:\n'
              '  py -m pip install --user keyring msal msal-extensions', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('\nInterrumpido. Lo traído hasta ahora se queda guardado.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
