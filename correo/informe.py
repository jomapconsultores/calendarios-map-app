# ------------------------------------------------------------
# Desarrollado por Marco Antonio Posligua San Martín
# ------------------------------------------------------------
"""Una página para mirar lo relacionado, sin abrir nueve buzones.

Tres bloques, en el orden en que importan: lo que tiene plazo por delante,
los correos que hablan de un quipux y las invitaciones.
Se abre con doble clic; no necesita servidor ni conexión.
"""
import html as _h
import os
from collections import defaultdict
from datetime import date, datetime

from . import almacen


def _fecha_corta(iso):
    try:
        return datetime.fromisoformat(iso).strftime('%d/%m/%Y %H:%M')
    except Exception:
        return (iso or '')[:16]


def _fila(m, extra=''):
    cuentas = ', '.join(c.split('@')[0] for c in m.get('cuentas') or [])
    return (f'<tr><td class="f">{_h.escape(_fecha_corta(m.get("fecha")))}</td>'
            f'<td>{_h.escape(m.get("de") or "")}</td>'
            f'<td><b>{_h.escape(m.get("asunto") or "(sin asunto)")}</b>'
            f'<div class="t">{_h.escape((m.get("texto") or "")[:220])}</div></td>'
            f'<td>{extra}</td><td class="c">{_h.escape(cuentas)} · #{m["id"]}</td></tr>')


def _tabla(titulo, filas, vacio):
    cuerpo = ''.join(filas) or f'<tr><td colspan="5" class="v">{vacio}</td></tr>'
    return (f'<h2>{titulo}</h2><table><tr><th>Fecha</th><th>De</th><th>Asunto</th>'
            f'<th>Relación</th><th>Cuenta</th></tr>{cuerpo}</table>')


def escribir(ruta=None, base=None):
    ruta = ruta or os.path.join(almacen.carpeta_por_defecto(), 'correo.html')
    hoy = date.today().isoformat()
    res = almacen.resumen(base)

    plazos = []
    for m in almacen.vinculados('plazo', desde=hoy, tope=300, ruta=base):
        v = next(v for v in m['vinculos'] if v['tipo'] == 'plazo')
        plazos.append((v['clave'], _fila(
            m, f'<span class="p">vence {_h.escape(v["clave"])}</span><br>'
               f'<small>{_h.escape(v["detalle"] or "")}</small>')))
    plazos = [f for _, f in sorted(plazos)]

    por_numero = defaultdict(list)
    for m in almacen.vinculados('quipux', tope=600, ruta=base):
        for v in m['vinculos']:
            if v['tipo'] == 'quipux':
                por_numero[(v['clave'], v['seguro'], v['detalle'])].append(m)
    quipux = []
    for (numero, seguro, detalle), mensajes in sorted(
            por_numero.items(), key=lambda kv: max(m['fecha'] or '' for m in kv[1]),
            reverse=True):
        marca = '' if seguro else ' <span class="d">sin recoger</span>'
        quipux.append(f'<tr class="g"><td colspan="5"><b>{_h.escape(numero)}</b>{marca} — '
                      f'{_h.escape(detalle or "")} ({len(mensajes)} correo(s))</td></tr>')
        quipux += [_fila(m) for m in mensajes[:10]]

    citas = []
    for m in almacen.vinculados('cita', tope=100, ruta=base):
        v = next(v for v in m['vinculos'] if v['tipo'] == 'cita')
        citas.append(_fila(m, _h.escape(v['detalle'] or '')))

    cuentas = ''.join(f'<li>{_h.escape(c)}: {n}</li>'
                      for c, n in sorted(res['por_cuenta'].items()))
    pagina = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<title>Correo — lo relacionado</title>
<style>
body{{font:14px system-ui,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%;margin-bottom:28px}}
th,td{{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top;text-align:left}}
th{{background:#f3f4f6}} .f{{white-space:nowrap}} .c{{color:#666;font-size:12px}}
.t{{color:#666;font-size:12px;margin-top:2px}} .v{{color:#888;font-style:italic}}
.p{{color:#b45309;font-weight:600}} .d{{background:#fde68a;padding:0 4px;font-size:11px}}
tr.g td{{background:#eef2ff}}
</style>
<h1>Correo — lo relacionado</h1>
<p>{res['total']} mensajes guardados · última pasada {_h.escape(res['ultima_pasada'] or '—')}</p>
<ul>{cuentas}</ul>
{_tabla('Con plazo por delante', plazos, 'Ningún correo con fecha de entrega pendiente.')}
{_tabla('Los que hablan de un quipux', quipux, 'Ningún correo cita un número de CuencaDOC.')}
{_tabla('Invitaciones de calendario', citas, 'Ninguna invitación.')}
</html>"""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(pagina)
    return ruta
