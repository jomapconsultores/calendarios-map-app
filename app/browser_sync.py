"""
Sincronización local de navegadores Chromium (Avast Secure Browser ⇄ Brave).

IMPORTANTE — modelo de ejecución:
  Este módulo LEE y ESCRIBE los perfiles locales de los navegadores en
  %LOCALAPPDATA%. Por lo tanto SÓLO funciona cuando la app corre en la misma
  máquina Windows donde están instalados Avast y Brave (uso local / localhost).
  En el servidor desplegado no hay perfiles que leer: por eso las rutas exigen
  BROWSER_SYNC_ENABLED=1, que sólo se pone en el .env de la máquina local.

Alcance honesto sobre CONTRASEÑAS:
  Chromium cifra las contraseñas en "Login Data" (SQLite). El esquema clásico
  `v10` usa una clave AES-256-GCM guardada en "Local State", envuelta con DPAPI
  del usuario de Windows. Ese caso SÍ lo migramos.
  Desde Chromium 127 existe App-Bound Encryption (blobs `v20` / clave `APPB`),
  ligada a la aplicación concreta para impedir justamente el trasvase entre
  navegadores. Esas entradas NO se pueden migrar con este método: se DETECTAN y
  se REPORTAN (no se pierden en silencio), pero no se descifran.

Seguridad de la bóveda:
  La clave adicional que el usuario teclea en cada sincronización deriva (scrypt)
  la llave AES-256-GCM con la que se cifra la bóveda local de contraseñas. Sin esa
  clave los datos en disco son ilegibles; nunca se guarda en sesión ni en la BD.
"""

import os
import json
import base64
import shutil
import sqlite3
import hashlib
import struct
import time
import ctypes
import ctypes.wintypes as wintypes
from datetime import datetime, timezone

# --- Dependencia de cifrado (opcional en import, obligatoria en ejecución) ---
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - entorno sin cryptography
    AESGCM = None
    CRYPTO_AVAILABLE = False

IS_WINDOWS = os.name == 'nt'


# ============================================================
#  Definición de navegadores soportados
# ============================================================
def _localappdata():
    return os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')


def _browser_defs():
    base = _localappdata()
    return {
        'avast': {
            'label': 'Avast Secure Browser',
            'user_data': os.path.join(base, 'AVAST Software', 'Browser', 'User Data'),
            'processes': ('AvastBrowser.exe',),
        },
        'brave': {
            'label': 'Brave',
            'user_data': os.path.join(base, 'BraveSoftware', 'Brave-Browser', 'User Data'),
            'processes': ('brave.exe',),
        },
    }


def _profile_dir(user_data, profile='Default'):
    return os.path.join(user_data, profile)


# ============================================================
#  DPAPI (ctypes) — sin dependencias externas
# ============================================================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi_decrypt(blob: bytes) -> bytes:
    """Descifra un blob DPAPI del usuario actual de Windows."""
    if not IS_WINDOWS:
        raise RuntimeError('DPAPI sólo está disponible en Windows.')
    buf_in = _DATA_BLOB(len(blob), ctypes.cast(ctypes.c_char_p(blob), ctypes.POINTER(ctypes.c_char)))
    buf_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None, 0, ctypes.byref(buf_out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(buf_out.pbData, buf_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(buf_out.pbData)


# ============================================================
#  Clave maestra AES desde "Local State"
# ============================================================
def _load_master_key(user_data):
    """
    Devuelve (key_bytes|None, info_dict). info incluye:
      has_v10_key: hay clave clásica descifrable con DPAPI.
      has_app_bound: existe app_bound_encrypted_key (v20 / APPB).
    """
    info = {'has_v10_key': False, 'has_app_bound': False, 'error': None}
    local_state = os.path.join(user_data, 'Local State')
    if not os.path.exists(local_state):
        info['error'] = 'No existe Local State'
        return None, info
    try:
        with open(local_state, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except Exception as e:
        info['error'] = f'Local State ilegible: {e}'
        return None, info

    oscrypt = (state.get('os_crypt') or {})
    info['has_app_bound'] = bool(oscrypt.get('app_bound_encrypted_key'))

    enc_key_b64 = oscrypt.get('encrypted_key')
    if not enc_key_b64:
        return None, info
    try:
        enc_key = base64.b64decode(enc_key_b64)
        if enc_key[:5] != b'DPAPI':
            info['error'] = 'encrypted_key sin prefijo DPAPI'
            return None, info
        key = _dpapi_decrypt(enc_key[5:])
        info['has_v10_key'] = True
        return key, info
    except Exception as e:
        info['error'] = f'No se pudo descifrar la clave maestra: {e}'
        return None, info


# ============================================================
#  Descifrado / cifrado de un valor de contraseña Chromium
# ============================================================
def _decrypt_value(blob: bytes, key: bytes):
    """
    Devuelve (texto|None, esquema). esquema ∈ {'v10','v20','dpapi','plain','error'}.
    'v20' = App-Bound: no migrable por este método.
    """
    if blob is None or blob == b'':
        return '', 'plain'
    prefix = blob[:3]
    if prefix == b'v20':
        return None, 'v20'          # App-Bound Encryption: no soportado
    if prefix == b'v10':
        if not key:
            return None, 'error'
        try:
            nonce = blob[3:15]
            ct = blob[15:]
            pt = AESGCM(key).decrypt(nonce, ct, None)
            return pt.decode('utf-8', 'replace'), 'v10'
        except Exception:
            return None, 'error'
    # Formato antiguo: DPAPI directo sobre el valor.
    try:
        return _dpapi_decrypt(blob).decode('utf-8', 'replace'), 'dpapi'
    except Exception:
        return None, 'error'


def _encrypt_value_v10(plaintext: str, key: bytes) -> bytes:
    """Cifra en formato `v10` con la clave AES del navegador DESTINO."""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode('utf-8'), None)
    return b'v10' + nonce + ct


# ============================================================
#  Utilidades de archivo / procesos
# ============================================================
def _is_running(process_names):
    """True si algún proceso con esos nombres está activo (tasklist)."""
    if not IS_WINDOWS:
        return False
    try:
        import subprocess
        out = subprocess.run(['tasklist', '/fo', 'csv', '/nh'],
                             capture_output=True, text=True, timeout=15).stdout.lower()
    except Exception:
        return False
    return any(p.lower() in out for p in process_names)


def _copy_locked(src):
    """Copia un archivo (posiblemente bloqueado por el navegador) a un temporal."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.sqlite')
    os.close(fd)
    shutil.copy2(src, tmp)
    return tmp


def _webkit_now():
    """Microsegundos desde 1601-01-01 (epoch de Windows/Chromium)."""
    epoch_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - epoch_1601
    return int(delta.total_seconds() * 1_000_000)


# ============================================================
#  BOOKMARKS
# ============================================================
def _flatten_bookmarks(node, acc):
    if not isinstance(node, dict):
        return
    if node.get('type') == 'url' and node.get('url'):
        acc[node['url']] = {'name': node.get('name', node['url']), 'url': node['url']}
    for child in node.get('children', []) or []:
        _flatten_bookmarks(child, acc)


def _read_bookmarks(profile_dir):
    path = os.path.join(profile_dir, 'Bookmarks')
    if not os.path.exists(path):
        return {}, path
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}, path
    acc = {}
    for root in ('bookmark_bar', 'other', 'synced'):
        _flatten_bookmarks((data.get('roots') or {}).get(root, {}), acc)
    return acc, path


def _write_bookmarks_merged(profile_dir, merged, backup_dir):
    """Escribe en 'bookmark_bar' los marcadores que falten (unión por URL)."""
    path = os.path.join(profile_dir, 'Bookmarks')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {'roots': {'bookmark_bar': {'children': [], 'name': 'Bookmarks bar',
                                           'type': 'folder'},
                          'other': {'children': [], 'name': 'Other', 'type': 'folder'},
                          'synced': {'children': [], 'name': 'Mobile', 'type': 'folder'}},
                'version': 1}
    existing, _ = _read_bookmarks(profile_dir)
    bar = data.setdefault('roots', {}).setdefault(
        'bookmark_bar', {'children': [], 'name': 'Bookmarks bar', 'type': 'folder'})
    bar.setdefault('children', [])
    ts = str(_webkit_now())
    added = 0
    for url, bm in merged.items():
        if url in existing:
            continue
        bar['children'].append({
            'date_added': ts, 'date_last_used': '0', 'guid': '', 'id': '',
            'name': bm['name'], 'type': 'url', 'url': url,
        })
        added += 1
    if added:
        _backup(path, backup_dir)
        # Chromium recalcula el checksum; borrarlo evita que descarte el archivo.
        data.pop('checksum', None)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    return added


# ============================================================
#  PASSWORDS
# ============================================================
def _read_passwords(profile_dir, key):
    """
    Devuelve (rows, stats). rows: lista de dicts con la contraseña ya en claro.
    stats: {'total','v10','v20','error'} para reporte honesto.
    """
    path = os.path.join(profile_dir, 'Login Data')
    stats = {'total': 0, 'v10': 0, 'v20': 0, 'error': 0}
    rows = []
    if not os.path.exists(path):
        return rows, stats
    tmp = _copy_locked(path)
    try:
        con = sqlite3.connect(tmp)
        con.row_factory = sqlite3.Row
        cur = con.execute(
            'SELECT origin_url, action_url, username_value, password_value, '
            'signon_realm, date_last_used, date_created FROM logins')
        for r in cur.fetchall():
            stats['total'] += 1
            pt, scheme = _decrypt_value(r['password_value'], key)
            if scheme == 'v20':
                stats['v20'] += 1
                continue
            if pt is None:
                stats['error'] += 1
                continue
            if scheme == 'v10':
                stats['v10'] += 1
            rows.append({
                'origin_url': r['origin_url'] or '',
                'action_url': r['action_url'] or '',
                'signon_realm': r['signon_realm'] or (r['origin_url'] or ''),
                'username_value': r['username_value'] or '',
                'password': pt,
                'date_last_used': r['date_last_used'] or 0,
                'date_created': r['date_created'] or 0,
            })
        con.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return rows, stats


def _merge_passwords(*row_lists):
    """Unión por (signon_realm, username). Ante conflicto gana el más reciente."""
    merged = {}
    for rows in row_lists:
        for r in rows:
            k = (r['signon_realm'], r['username_value'])
            cur = merged.get(k)
            if cur is None or (r['date_last_used'] or 0) > (cur['date_last_used'] or 0):
                merged[k] = r
    return merged


def _inject_passwords(profile_dir, merged, key, backup_dir):
    """
    Escribe las contraseñas fusionadas en el Login Data del navegador destino,
    re-cifrando cada valor con SU clave (`v10`). El navegador DEBE estar cerrado.
    Devuelve {'inserted','updated','failed'}.
    """
    path = os.path.join(profile_dir, 'Login Data')
    if not os.path.exists(path):
        return {'inserted': 0, 'updated': 0, 'failed': 0, 'error': 'sin Login Data'}
    _backup(path, backup_dir)
    res = {'inserted': 0, 'updated': 0, 'failed': 0}
    con = sqlite3.connect(path)
    try:
        cols = {row[1] for row in con.execute('PRAGMA table_info(logins)')}
        now = _webkit_now()
        for (realm, user), r in merged.items():
            try:
                enc = _encrypt_value_v10(r['password'], key)
                exists = con.execute(
                    'SELECT 1 FROM logins WHERE signon_realm=? AND username_value=?',
                    (realm, user)).fetchone()
                if exists:
                    con.execute(
                        'UPDATE logins SET password_value=? WHERE signon_realm=? '
                        'AND username_value=?', (enc, realm, user))
                    res['updated'] += 1
                else:
                    base_cols = {
                        'origin_url': r['origin_url'] or realm,
                        'action_url': r['action_url'] or '',
                        'username_element': '',
                        'username_value': user,
                        'password_element': '',
                        'password_value': enc,
                        'signon_realm': realm,
                        'date_created': r['date_created'] or now,
                        'blacklisted_by_user': 0,
                        'scheme': 0,
                        'password_type': 0,
                        'times_used': 0,
                        'date_last_used': now,
                    }
                    use = {c: v for c, v in base_cols.items() if c in cols}
                    placeholders = ','.join('?' for _ in use)
                    con.execute(
                        f"INSERT INTO logins ({','.join(use)}) VALUES ({placeholders})",
                        list(use.values()))
                    res['inserted'] += 1
            except Exception:
                res['failed'] += 1
        con.commit()
    finally:
        con.close()
    return res


# ============================================================
#  Bóveda cifrada + verificador de clave (scrypt + AES-GCM)
# ============================================================
def _data_dir():
    """Directorio de la bóveda/verificador. Multiplataforma para que el flujo
    CSV funcione también en el servidor Linux (donde no hay LOCALAPPDATA).
    Se puede forzar con la variable de entorno BROWSER_SYNC_DATA_DIR."""
    override = os.environ.get('BROWSER_SYNC_DATA_DIR')
    if override:
        base = override
    elif IS_WINDOWS:
        base = os.path.join(_localappdata(), 'calendarios-map', 'browser_sync')
    else:
        base = os.path.join(
            os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'),
            'calendarios-map', 'browser_sync')
    os.makedirs(base, exist_ok=True)
    return base


def _backup_dir():
    d = os.path.join(_data_dir(), 'backups',
                     datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(d, exist_ok=True)
    return d


def _backup(path, backup_dir):
    try:
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
    except Exception:
        pass


_SCRYPT = dict(n=2 ** 15, r=8, p=1, dklen=32)
# scrypt necesita un tope de memoria explícito: 128*n*r*p + margen.
_SCRYPT_MAXMEM = 128 * _SCRYPT['n'] * _SCRYPT['r'] * _SCRYPT['p'] * 2


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode('utf-8'), salt=salt,
                          maxmem=_SCRYPT_MAXMEM, **_SCRYPT)


def _verifier_path():
    return os.path.join(_data_dir(), 'verifier.json')


def _vault_path():
    return os.path.join(_data_dir(), 'vault.bin')


def vault_exists():
    return os.path.exists(_verifier_path())


def _set_or_check_passphrase(passphrase: str):
    """
    Si es la primera vez, fija la clave (crea verificador). Si ya existe, la valida.
    Devuelve (ok: bool, salt: bytes, is_new: bool).
    """
    vp = _verifier_path()
    if not os.path.exists(vp):
        salt = os.urandom(16)
        key = _derive_key(passphrase, salt)
        verifier = hashlib.sha256(b'verify' + key).hexdigest()
        with open(vp, 'w', encoding='utf-8') as f:
            json.dump({'salt': base64.b64encode(salt).decode(),
                       'verifier': verifier, 'kdf': 'scrypt'}, f)
        return True, salt, True
    with open(vp, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    salt = base64.b64decode(meta['salt'])
    key = _derive_key(passphrase, salt)
    ok = hashlib.sha256(b'verify' + key).hexdigest() == meta['verifier']
    return ok, salt, False


def _save_vault_records(records, key):
    """Cifra la bóveda (lista de credenciales) con AES-256-GCM."""
    payload = json.dumps(records, ensure_ascii=False).encode('utf-8')
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, payload, None)
    with open(_vault_path(), 'wb') as f:
        f.write(nonce + ct)


def _load_vault_records(key):
    """Descifra la bóveda. Devuelve [] si no existe. Lanza si la clave no cuadra."""
    vp = _vault_path()
    if not os.path.exists(vp):
        return []
    with open(vp, 'rb') as f:
        blob = f.read()
    pt = AESGCM(key).decrypt(blob[:12], blob[12:], None)
    return json.loads(pt.decode('utf-8'))


def _record_key(rec):
    """Clave de deduplicación: (url, usuario) normalizados."""
    return ((rec.get('url') or '').strip().lower().rstrip('/'),
            (rec.get('username') or '').strip())


def _merge_records(*lists):
    """Une listas de credenciales por (url, usuario). Gana la última con contraseña."""
    out = {}
    for lst in lists:
        for rec in lst:
            k = _record_key(rec)
            if not k[0] and not k[1]:
                continue
            cur = out.get(k)
            if cur is None or (rec.get('password') and not cur.get('password')):
                out[k] = dict(rec)
            elif rec.get('password'):
                out[k] = dict(rec)  # última escritura gana
    return list(out.values())


# ============================================================
#  Estado (para pintar la pantalla)
# ============================================================
def status():
    defs = _browser_defs()
    out = {'is_windows': IS_WINDOWS, 'crypto_available': CRYPTO_AVAILABLE,
           'vault_exists': vault_exists(), 'browsers': {}}
    for bid, d in defs.items():
        ud = d['user_data']
        prof = _profile_dir(ud)
        installed = os.path.isdir(ud)
        info = {}
        if installed:
            _, info = _load_master_key(ud)
        out['browsers'][bid] = {
            'label': d['label'],
            'installed': installed,
            'running': _is_running(d['processes']) if installed else False,
            'has_login_data': os.path.exists(os.path.join(prof, 'Login Data')),
            'has_bookmarks': os.path.exists(os.path.join(prof, 'Bookmarks')),
            'has_v10_key': info.get('has_v10_key', False),
            'has_app_bound': info.get('has_app_bound', False),
        }
    return out


# ============================================================
#  Orquestador principal
# ============================================================
def run_sync(passphrase, do_bookmarks=True, do_passwords=True, profile='Default'):
    """
    Ejecuta la sincronización bidireccional (unión) entre Avast y Brave.
    Devuelve un reporte dict con todo lo ocurrido (para mostrar al usuario).
    """
    report = {'ok': False, 'messages': [], 'bookmarks': {}, 'passwords': {},
              'skipped_app_bound': {}, 'backup_dir': None}

    if not IS_WINDOWS:
        report['messages'].append('Sólo funciona en la máquina Windows local.')
        return report
    if not CRYPTO_AVAILABLE:
        report['messages'].append("Falta la librería 'cryptography'. Instala: pip install cryptography")
        return report
    if not passphrase or len(passphrase) < 8:
        report['messages'].append('La clave adicional debe tener al menos 8 caracteres.')
        return report

    ok, salt, is_new = _set_or_check_passphrase(passphrase)
    if not ok:
        report['messages'].append('Clave adicional incorrecta.')
        return report
    vault_key = _derive_key(passphrase, salt)
    if is_new:
        report['messages'].append('Clave adicional establecida por primera vez.')

    defs = _browser_defs()
    installed = {b: d for b, d in defs.items() if os.path.isdir(d['user_data'])}
    if len(installed) < 2:
        report['messages'].append('Se necesitan ambos navegadores instalados (Avast y Brave).')
        return report

    backup_dir = _backup_dir()
    report['backup_dir'] = backup_dir

    # ---- BOOKMARKS ----
    if do_bookmarks:
        all_bm = {}
        for b, d in installed.items():
            bm, _ = _read_bookmarks(_profile_dir(d['user_data'], profile))
            all_bm.update(bm)
        for b, d in installed.items():
            added = _write_bookmarks_merged(_profile_dir(d['user_data'], profile),
                                            all_bm, backup_dir)
            report['bookmarks'][b] = {'total_merged': len(all_bm), 'added': added}
        report['messages'].append(f'Marcadores: {len(all_bm)} únicos fusionados.')

    # ---- PASSWORDS ----
    if do_passwords:
        # 1) Ningún navegador puede estar abierto (escribimos su SQLite).
        running = [d['label'] for b, d in installed.items() if _is_running(d['processes'])]
        if running:
            report['messages'].append(
                'Cierra estos navegadores antes de sincronizar contraseñas: '
                + ', '.join(running))
            report['ok'] = do_bookmarks  # los marcadores sí se hicieron
            return report

        keys = {}
        per_browser_rows = {}
        for b, d in installed.items():
            key, info = _load_master_key(d['user_data'])
            keys[b] = key
            rows, stats = _read_passwords(_profile_dir(d['user_data'], profile), key)
            per_browser_rows[b] = rows
            report['passwords'][b] = {'read': stats}
            if stats['v20']:
                report['skipped_app_bound'][b] = stats['v20']

        merged = _merge_passwords(*per_browser_rows.values())
        # Alimenta la bóveda (formato unificado con la ruta CSV).
        v10_records = [{'name': '', 'url': r['origin_url'],
                        'username': r['username_value'], 'password': r['password'],
                        'note': ''} for r in merged.values() if r['password']]
        try:
            existing = _load_vault_records(vault_key)
        except Exception:
            existing = []
        all_records = _merge_records(existing, v10_records)
        _save_vault_records(all_records, vault_key)
        report['messages'].append(
            f'Bóveda cifrada actualizada: {len(all_records)} credenciales.')

        for b, d in installed.items():
            if not keys.get(b):
                report['passwords'][b]['write'] = {'error': 'sin clave v10'}
                continue
            res = _inject_passwords(_profile_dir(d['user_data'], profile),
                                    merged, keys[b], backup_dir)
            report['passwords'][b]['write'] = res

        total_v20 = sum(report['skipped_app_bound'].values())
        if total_v20:
            report['messages'].append(
                f'{total_v20} contraseñas con App-Bound (v20) NO migrables por este '
                'método: cópialas manualmente o usa la importación del navegador.')

    report['ok'] = True
    return report


# ============================================================
#  CSV — flujo robusto a prueba de App-Bound (v20)
#  El usuario exporta el CSV desde cada navegador (Ajustes → Contraseñas →
#  Exportar) y la app lo fusiona en la bóveda cifrada, devolviendo un CSV
#  unificado para reimportar en ambos. No requiere descifrar nada del navegador.
# ============================================================
_CSV_FIELDS = ['name', 'url', 'username', 'password', 'note']


def parse_password_csv(text):
    """Parsea un CSV de contraseñas de Chromium a lista de registros."""
    import csv, io as _io
    if text and text[0] == '﻿':          # quita BOM
        text = text[1:]
    reader = csv.DictReader(_io.StringIO(text))
    recs = []
    for row in reader:
        low = {(k or '').strip().lower(): (v or '') for k, v in row.items()}
        rec = {
            'name': low.get('name', ''),
            'url': low.get('url', low.get('origin', '')),
            'username': low.get('username', low.get('login', '')),
            'password': low.get('password', ''),
            'note': low.get('note', low.get('notes', '')),
        }
        if rec['url'] or rec['username']:
            recs.append(rec)
    return recs


def import_password_csvs(passphrase, csv_texts):
    """Fusiona uno o más CSV con la bóveda existente. Devuelve reporte."""
    rep = {'ok': False, 'messages': [], 'parsed': 0, 'total': 0}
    if not CRYPTO_AVAILABLE:
        rep['messages'].append("Falta la librería 'cryptography'.")
        return rep
    if not passphrase or len(passphrase) < 8:
        rep['messages'].append('La clave adicional debe tener al menos 8 caracteres.')
        return rep
    ok, salt, is_new = _set_or_check_passphrase(passphrase)
    if not ok:
        rep['messages'].append('Clave adicional incorrecta.')
        return rep
    key = _derive_key(passphrase, salt)
    parsed = []
    for t in csv_texts:
        try:
            parsed.extend(parse_password_csv(t))
        except Exception as e:
            rep['messages'].append(f'CSV ilegible: {e}')
    rep['parsed'] = len(parsed)
    try:
        existing = _load_vault_records(key) if not is_new else []
    except Exception:
        rep['messages'].append('No se pudo abrir la bóveda con esa clave.')
        return rep
    merged = _merge_records(existing, parsed)
    _save_vault_records(merged, key)
    rep['total'] = len(merged)
    rep['ok'] = True
    rep['messages'].append(
        f'{len(parsed)} credenciales importadas · bóveda: {len(merged)} únicas.')
    return rep


def export_password_csv(passphrase):
    """Descifra la bóveda y devuelve (csv_bytes|None, error|None)."""
    import csv, io as _io
    if not CRYPTO_AVAILABLE:
        return None, "Falta la librería 'cryptography'."
    if not vault_exists():
        return None, 'Aún no hay bóveda: importa primero los CSV.'
    ok, salt, _ = _set_or_check_passphrase(passphrase)
    if not ok:
        return None, 'Clave adicional incorrecta.'
    key = _derive_key(passphrase, salt)
    try:
        records = _load_vault_records(key)
    except Exception:
        return None, 'No se pudo abrir la bóveda con esa clave.'
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction='ignore')
    w.writeheader()
    for r in records:
        w.writerow({k: r.get(k, '') for k in _CSV_FIELDS})
    return ('﻿' + buf.getvalue()).encode('utf-8'), None
