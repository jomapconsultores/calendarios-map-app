from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from config.config import Config
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import google.auth.exceptions
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict, OrderedDict
import os, requests as req_lib, traceback, pytz, json, re, time, calendar as _cal, io
from urllib.parse import quote as _url_quote

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

GOOGLE_SCOPES = ['https://www.googleapis.com/auth/calendar']

# Microsoft Graph — To-Do
MS_AUTH_URL   = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize'
MS_TOKEN_URL  = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token'
MS_GRAPH_URL  = 'https://graph.microsoft.com/v1.0'
MS_SCOPES     = 'Tasks.ReadWrite offline_access User.Read'
GOOGLE_ACCOUNT_EMAIL = 'mposligua0000@gmail.com'

# WebAuthn / passkeys (Face ID, huella). Import protegido: si la librería aún
# no está instalada, la app arranca igual y la función queda deshabilitada.
try:
    from webauthn import (
        generate_registration_options, verify_registration_response,
        generate_authentication_options, verify_authentication_response,
        options_to_json,
    )
    from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria, ResidentKeyRequirement,
        UserVerificationRequirement, PublicKeyCredentialDescriptor,
    )
    WEBAUTHN_AVAILABLE = True
except Exception as _wa_err:  # pragma: no cover
    WEBAUTHN_AVAILABLE = False
    print(f'[webauthn] no disponible: {_wa_err}')

load_dotenv()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
TIMEZONE = pytz.timezone('America/Guayaquil')
login_manager = LoginManager()


# ============================================================
#  TTL CACHE — in-process, thread-safe via GIL for CPython
# ============================================================
class TTLCache:
    """Lightweight TTL cache with LRU eviction."""
    def __init__(self, ttl=60, maxsize=256):
        self._data = OrderedDict()
        self._ts = {}
        self.ttl = ttl
        self.maxsize = maxsize

    def get(self, key):
        if key in self._data:
            if time.monotonic() - self._ts[key] < self.ttl:
                self._data.move_to_end(key)
                return self._data[key], True
            self._evict(key)
        return None, False

    def set(self, key, value):
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        self._ts[key] = time.monotonic()
        if len(self._data) > self.maxsize:
            oldest = next(iter(self._data))
            self._evict(oldest)

    def _evict(self, key):
        self._data.pop(key, None)
        self._ts.pop(key, None)

    def invalidate(self, key):
        self._evict(key)

    def invalidate_prefix(self, prefix):
        for k in [k for k in self._data if k.startswith(prefix)]:
            self._evict(k)

# Module-level caches (shared across requests in same worker)
_cal_cache      = TTLCache(ttl=300)   # calendar_config — 5 min
_user_cal_cache = TTLCache(ttl=10)    # user calendars  — 10 s (corto: multi-worker safe)
_google_cache   = TTLCache(ttl=120)   # google status   — 2 min


# ============================================================
#  USER MODEL
# ============================================================
class User(UserMixin):
    def __init__(self, d):
        self.id = d.get('id'); self.email = d.get('email')
        self.full_name = d.get('full_name'); self.role = d.get('role', 'staff')
        self.is_admin = d.get('role') == 'admin'
        raw = d.get('modules', 'calendar,planning') or 'calendar,planning'
        self.modules = [m.strip() for m in raw.split(',') if m.strip()]

ALL_MODULES = [
    ('calendar',  '📅 Calendario'),
    ('planning',  '📋 Planificación'),
    ('todo',      '✅ To-Do externo (Microsoft)'),
]


# ============================================================
#  SUPABASE CLIENT — persistent HTTP session (keep-alive)
# ============================================================
class SupabaseAPI:
    def __init__(self, url, key):
        self.url = url
        self._session = req_lib.Session()
        self._headers = {
            'apikey': key,
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        }
        self._session.headers.update(self._headers)
        # Set sane timeouts for all calls
        self._timeout = (4, 10)   # (connect, read)

    def get(self, table, filters=None, select='*'):
        q = f'{self.url}/rest/v1/{table}?select={select}'
        if filters:
            for k, v in filters.items():
                q += f'&{k}=eq.{v}'
        try:
            r = self._session.get(q, timeout=self._timeout)
            return r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f'[supabase.get] {table}: {e}')
            return []

    def get_in(self, table, column, values, select='*'):
        """Single query WHERE column IN (values)."""
        if not values:
            return []
        ids = ','.join(str(v) for v in values)
        q = f'{self.url}/rest/v1/{table}?select={select}&{column}=in.({ids})'
        try:
            r = self._session.get(q, timeout=self._timeout)
            return r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f'[supabase.get_in] {table}: {e}')
            return []

    def insert(self, table, data):
        h = {'Prefer': 'return=representation'}
        try:
            r = self._session.post(f'{self.url}/rest/v1/{table}', headers=h, json=data, timeout=self._timeout)
            if r.status_code in [200, 201]:
                body = r.json()
                return body if isinstance(body, list) else [body]
            return None
        except Exception as e:
            print(f'[supabase.insert] {table}: {e}')
            return None

    def insert_ignore(self, table, data):
        """Insert and silently ignore unique-constraint conflicts."""
        h = {'Prefer': 'resolution=ignore-duplicates,return=minimal'}
        try:
            r = self._session.post(f'{self.url}/rest/v1/{table}', headers=h, json=data, timeout=self._timeout)
            return r.status_code in [200, 201, 204]
        except Exception as e:
            print(f'[supabase.insert_ignore] {table}: {e}')
            return False

    def update(self, table, id_val, data, id_col='id'):
        h = {'Prefer': 'return=minimal'}
        try:
            r = self._session.patch(
                f'{self.url}/rest/v1/{table}?{id_col}=eq.{id_val}',
                headers=h, json=data, timeout=self._timeout)
            return r.status_code in [200, 204]
        except Exception as e:
            print(f'[supabase.update] {table}: {e}')
            return False

    def delete(self, table, id_val, id_col='id'):
        h = {'Prefer': 'return=minimal'}
        try:
            r = self._session.delete(
                f'{self.url}/rest/v1/{table}?{id_col}=eq.{id_val}',
                headers=h, timeout=self._timeout)
            return r.status_code in [200, 204]
        except Exception as e:
            print(f'[supabase.delete] {table}: {e}')
            return False

    def get_q(self, table, query_params=None, select='*'):
        """Query with raw PostgREST filter params, e.g. {'status': 'eq.done'}."""
        q = f'{self.url}/rest/v1/{table}?select={select}'
        for k, v in (query_params or {}).items():
            q += f'&{k}={v}'
        try:
            r = self._session.get(q, timeout=self._timeout)
            return r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f'[supabase.get_q] {table}: {e}')
            return []


# ============================================================
#  HELPERS
# ============================================================
def _is_invalid_grant(err):
    s = str(err).lower()
    return 'invalid_grant' in s or 'expired or revoked' in s or 'token has been expired' in s

def _sanitize(s, max_len=255):
    return str(s).strip()[:max_len] if s else ''

def _validate_email(email):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email or ''))


# ============================================================
#  GOOGLE CALENDAR EVENT BUILDER
# ============================================================
def _build_google_event(apt, attendees):
    """Build a Google Calendar event body, properly handling virtual vs presencial."""
    is_virtual = bool(apt.get('meeting_link'))
    desc = (f"Titulo: {apt.get('title', '')}\n"
            f"Encargado: {apt.get('encargado', '')}\n"
            f"Tema: {apt.get('tema', '')}")
    if apt.get('client_name'): desc += f"\nCliente: {apt['client_name']}"

    if is_virtual:
        desc += f"\n\n🔗 Enlace de reunion: {apt['meeting_link']}"
        location = apt['meeting_link']
    else:
        if apt.get('lugar'):     desc += f"\nLugar: {apt['lugar']}"
        if apt.get('direccion'): desc += f"\nDireccion: {apt['direccion']}"
        if apt.get('ciudad'):    desc += f"\nCiudad: {apt['ciudad']}, Ecuador"
        if apt.get('mapa'):      desc += f"\n📍 Mapa: {apt['mapa']}"
        location = ''
        if apt.get('direccion'):
            location = apt['direccion']
            if apt.get('ciudad'): location += f", {apt['ciudad']}, Ecuador"
            if apt.get('lugar'):  location = f"{apt['lugar']}, {location}"
        elif apt.get('lugar'):
            location = apt['lugar']

    if apt.get('notes'): desc += f"\nNotas: {apt['notes']}"

    event = {
        'summary': f"{apt.get('title', '')} - {apt.get('encargado', '')}",
        'description': desc,
        'start': {'dateTime': apt['start_time'], 'timeZone': 'America/Guayaquil'},
        'end':   {'dateTime': apt['end_time'],   'timeZone': 'America/Guayaquil'},
        'attendees': attendees,
        'reminders': {'useDefault': False, 'overrides': [
            {'method': 'email', 'minutes': 1440},
            {'method': 'popup', 'minutes': 30}]},
    }
    if location: event['location'] = location
    return event


# ============================================================
#  MICROSOFT TOKEN HELPER
# ============================================================
def _refresh_ms_token(app, t):
    """Refresh a single MS token row. Returns new access_token or None."""
    try:
        r = req_lib.post(MS_TOKEN_URL, data={
            'client_id':     app.config.get('MS_CLIENT_ID', ''),
            'client_secret': app.config.get('MS_CLIENT_SECRET', ''),
            'grant_type':    'refresh_token',
            'refresh_token': t.get('refresh_token', ''),
            'scope': MS_SCOPES,
        }, timeout=(5, 15))
        if r.status_code != 200:
            return None
        d = r.json()
        new_exp = (datetime.now(timezone.utc)
                   + timedelta(seconds=d.get('expires_in', 3600))).isoformat()
        app.supabase.update('ms_tokens', t['id'], {
            'access_token':  d['access_token'],
            'refresh_token': d.get('refresh_token', t['refresh_token']),
            'expires_at':    new_exp,
        })
        return d['access_token']
    except Exception:
        return None


def get_ms_token(app):
    """Return a valid MS Graph access_token for the first connected account."""
    tokens = app.supabase.get('ms_tokens', select='*')
    if not tokens: return None
    t = tokens[0]
    expiry_str = t.get('expires_at')
    if expiry_str:
        try:
            exp = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                return _refresh_ms_token(app, t)
        except Exception:
            pass
    return t.get('access_token')


def get_ms_token_for(app, ms_email):
    """Token válido (refresca si toca) para una cuenta MS específica."""
    rows = app.supabase.get('ms_tokens', {'email': ms_email}, select='*')
    if not rows: return None
    t = rows[0]
    expiry_str = t.get('expires_at')
    if expiry_str:
        try:
            exp = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                return _refresh_ms_token(app, t)
        except Exception:
            pass
    return t.get('access_token')


# Mapeos de estado/prioridad para empujar a MS
_TO_MS_STATUS = {'pending':'notStarted','in_progress':'inProgress',
                 'review':'waitingOnOthers','done':'completed','blocked':'deferred'}
_TO_MS_PRIO   = {'low':'low','medium':'normal','high':'high','urgent':'high'}

def _build_ms_task_body(task):
    body = {
        'title':      task.get('title','')[:255],
        'importance': _TO_MS_PRIO.get(task.get('priority'), 'normal'),
        'status':     _TO_MS_STATUS.get(task.get('status','pending'), 'notStarted'),
    }
    if task.get('description'):
        body['body'] = {'content': task['description'], 'contentType':'text'}
    if task.get('due_date'):
        body['dueDateTime'] = {'dateTime': f"{task['due_date']}T23:59:00",
                               'timeZone': 'America/Guayaquil'}
    return body

def push_task_to_ms(app, task):
    """Empuja un cambio del sistema a Microsoft To-Do.
    Devuelve (success: bool, new_source_id: str|None). Si se crea una tarea
    nueva en MS, new_source_id trae el ID asignado por Graph."""
    ms_email = task.get('ms_email'); list_id = task.get('ms_list_id')
    src_id   = task.get('source_id')
    if not (ms_email and list_id): return (False, None)
    token = get_ms_token_for(app, ms_email)
    if not token: return (False, None)
    headers = {'Authorization': f'Bearer {token}','Content-Type':'application/json'}
    try:
        if src_id:
            r = req_lib.patch(f'{MS_GRAPH_URL}/me/todo/lists/{list_id}/tasks/{src_id}',
                              headers=headers, json=_build_ms_task_body(task), timeout=(5,15))
            return (r.status_code in (200, 204), None)
        else:
            r = req_lib.post(f'{MS_GRAPH_URL}/me/todo/lists/{list_id}/tasks',
                             headers=headers, json=_build_ms_task_body(task), timeout=(5,15))
            if r.status_code in (200, 201):
                return (True, r.json().get('id'))
            return (False, None)
    except Exception:
        return (False, None)

def delete_task_in_ms(app, task):
    ms_email = task.get('ms_email'); list_id = task.get('ms_list_id')
    src_id   = task.get('source_id')
    if task.get('source') != 'ms_todo' or not (ms_email and list_id and src_id): return False
    token = get_ms_token_for(app, ms_email)
    if not token: return False
    try:
        r = req_lib.delete(f'{MS_GRAPH_URL}/me/todo/lists/{list_id}/tasks/{src_id}',
                           headers={'Authorization': f'Bearer {token}'}, timeout=(5,15))
        return r.status_code in (200, 204)
    except Exception:
        return False


def get_all_ms_tokens(app):
    """Return list of (email, access_token) for every connected MS account.
    Refreshes expired tokens automatically. Skips accounts whose refresh fails."""
    tokens = app.supabase.get('ms_tokens', select='*')
    out = []
    for t in tokens:
        access = t.get('access_token')
        expiry_str = t.get('expires_at')
        if expiry_str:
            try:
                exp = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                    access = _refresh_ms_token(app, t)
            except Exception:
                pass
        if access:
            out.append((t.get('email', 'microsoft'), access))
    return out


# ============================================================
#  GOOGLE CREDENTIALS
# ============================================================
def get_google_creds(app):
    try:
        tokens = app.supabase.get('google_tokens', {'email': GOOGLE_ACCOUNT_EMAIL})
        if not tokens:
            return None
        t = tokens[0]
        expiry = None
        if t.get('token_expiry'):
            try:
                expiry = datetime.fromisoformat(t['token_expiry'].replace('Z', '+00:00'))
                if expiry.tzinfo is not None:
                    expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                expiry = None
        creds = Credentials(
            token=t.get('token'), refresh_token=t.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=app.config['GOOGLE_CLIENT_ID'],
            client_secret=app.config['GOOGLE_CLIENT_SECRET'],
            scopes=GOOGLE_SCOPES, expiry=expiry)
        if not creds.refresh_token:
            return None
        needs_refresh = (expiry is None) or creds.expired
        if not needs_refresh and expiry is not None:
            try:
                if expiry <= datetime.utcnow() + timedelta(minutes=5):
                    needs_refresh = True
            except Exception:
                needs_refresh = True
        if needs_refresh:
            try:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                _save_token_fields(app, t['id'], creds)
            except google.auth.exceptions.RefreshError:
                return None
            except Exception:
                return None
        return creds
    except Exception:
        return None

def _save_token_fields(app, token_id, creds):
    data = {'token': creds.token, 'refresh_token': creds.refresh_token}
    if creds.expiry:
        data['token_expiry'] = creds.expiry.isoformat()
    if not app.supabase.update('google_tokens', token_id, data) and creds.expiry:
        app.supabase.update('google_tokens', token_id, {'token': creds.token, 'refresh_token': creds.refresh_token})

def save_google_creds(app, creds):
    refresh_token = creds.refresh_token
    if not refresh_token:
        prev = app.supabase.get('google_tokens', {'email': GOOGLE_ACCOUNT_EMAIL})
        if prev and prev[0].get('refresh_token'):
            refresh_token = prev[0]['refresh_token']
    app.supabase.delete('google_tokens', GOOGLE_ACCOUNT_EMAIL, 'email')
    data = {'email': GOOGLE_ACCOUNT_EMAIL, 'token': creds.token, 'refresh_token': refresh_token}
    if creds.expiry:
        data['token_expiry'] = creds.expiry.isoformat()
    result = app.supabase.insert('google_tokens', data)
    if not result and creds.expiry:
        del data['token_expiry']
        app.supabase.insert('google_tokens', data)
    _google_cache.invalidate_prefix('google_status_')  # bust cache on reconnect


# ============================================================
#  CALENDAR ACCESS (with caching)
# ============================================================
def _get_calendar_config(app):
    """Cached calendar_config (5 min)."""
    val, hit = _cal_cache.get('all')
    if hit:
        return val
    result = app.supabase.get('calendar_config', select='calendar_id,name,email,color,google_cal_id')
    _cal_cache.set('all', result)
    return result

def _make_cal_maps(all_cals):
    """Build two maps from calendar_config list.
    Returns (email_map, gcal_id_map):
      email_map   — calendar_id → contact email (attendee)
      gcal_id_map — calendar_id → Google Calendar ID to use for events
    """
    email_map   = {c['calendar_id']: c['email']
                   for c in all_cals if c.get('email')}
    gcal_id_map = {c['calendar_id']: (c.get('google_cal_id') or 'primary')
                   for c in all_cals}
    return email_map, gcal_id_map

def _build_attendees(apt, email_map):
    """Build a deduplicated attendee list for a Google Calendar event.
    Uses lowercase comparison to avoid case-sensitive duplicates.
    """
    seen = set()
    attendees = []
    def _add(email):
        e = (email or '').strip().lower()
        if e and e not in seen:
            seen.add(e)
            attendees.append({'email': email.strip()})
    cal_email = email_map.get(apt.get('calendar_id', ''))
    if cal_email:
        _add(cal_email)
    if apt.get('invitados'):
        for inv in apt['invitados'].split(','):
            _add(inv)
    if not attendees:
        _add(GOOGLE_ACCOUNT_EMAIL)
    return attendees

def get_user_calendars(app, uid):
    """Cached user calendars (90 s)."""
    val, hit = _user_cal_cache.get(uid)
    if hit:
        return val
    perms = app.supabase.get('calendar_permissions',
        {'user_id': uid, 'status': 'approved'}, select='calendar_id')
    cal_ids = {p['calendar_id'] for p in perms}
    all_cals = _get_calendar_config(app)
    result = [c for c in all_cals if c['calendar_id'] in cal_ids]
    _user_cal_cache.set(uid, result)
    return result

def user_has_calendar_access(app, uid, calendar_id):
    return any(c['calendar_id'] == calendar_id for c in get_user_calendars(app, uid))

def is_admin():
    return current_user.is_authenticated and current_user.role == 'admin'

def get_user_ms_emails(app, uid):
    """Lista de cuentas MS que el usuario tiene autorizadas (admins ven todas)."""
    rows = app.supabase.get('ms_account_permissions', {'user_id': uid}, select='ms_email')
    return [r['ms_email'] for r in (rows or [])]

def user_can(module):
    """True si el usuario tiene acceso al módulo (admins siempre sí)."""
    if is_admin(): return True
    return module in getattr(current_user, 'modules', [])


# ============================================================
#  APPOINTMENT BUILDER
# ============================================================
def _build_appointment(title, cal_id, encargado, tema, client_name, client_email,
                        start_dt, end_dt, tipo, link, lugar, direccion, mapa,
                        ciudad, notificar, notes, user_id):
    return {
        'title': title, 'calendar_id': cal_id, 'encargado': encargado, 'tema': tema,
        'client_name': client_name, 'client_email': client_email,
        'start_time': start_dt.isoformat(), 'end_time': end_dt.isoformat(),
        'status': 'pending', 'notes': notes,
        'invitados': ','.join(notificar) if notificar else '',
        'lugar': lugar, 'direccion': direccion, 'mapa': mapa, 'ciudad': ciudad,
        'meeting_link': link if tipo == 'virtual' else '',
        'created_by': user_id,
    }


# ============================================================
#  RECURRENCE — flexible occurrence generator (materialized)
# ============================================================
# Tope de seguridad: nº máximo de eventos materializados por serie.
# Para recurrencia "indefinida" se materializa hasta este tope.
REC_HARD_CAP = 366


def _add_months(d, months):
    """Suma `months` a la fecha `d`. Devuelve None si el día no existe
    en el mes destino (ej. 31 en un mes de 30 días) — se omite la ocurrencia."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = _cal.monthrange(y, m)[1]
    if d.day > last:
        return None
    return date(y, m, d.day)


def _generate_recurrence_dates(start_d, freq, interval, weekdays,
                               end_mode, end_date, count, cap=REC_HARD_CAP):
    """Genera la lista de fechas de una serie recurrente.

    freq:     'daily' | 'weekly' | 'monthly' | 'yearly'
    interval: cada N (días/semanas/meses/años), entero >= 1
    weekdays: lista de días [0=Lun..6=Dom] — solo para 'weekly'
    end_mode: 'until' (hasta end_date) | 'count' (N ocurrencias) | 'forever'
    """
    interval = max(1, int(interval or 1))
    count = max(1, int(count or 1))
    out = []

    def _reached_limit():
        if end_mode == 'count' and len(out) >= count:
            return True
        return len(out) >= cap

    if freq == 'daily':
        k = 0
        while len(out) < cap:
            d = start_d + timedelta(days=k * interval)
            if end_mode == 'until' and d > end_date:
                break
            out.append(d)
            if _reached_limit():
                break
            k += 1

    elif freq == 'weekly':
        wds = sorted(set(weekdays)) if weekdays else [start_d.weekday()]
        start_monday = start_d - timedelta(days=start_d.weekday())
        wk = 0
        stop = False
        while len(out) < cap and not stop:
            week_start = start_monday + timedelta(weeks=wk * interval)
            for wd in wds:
                d = week_start + timedelta(days=wd)
                if d < start_d:
                    continue
                if end_mode == 'until' and d > end_date:
                    stop = True
                    break
                out.append(d)
                if _reached_limit():
                    stop = True
                    break
            wk += 1

    elif freq == 'monthly':
        k = 0
        guard = 0
        while len(out) < cap and guard < cap * 3:
            guard += 1
            ref = _add_months(start_d.replace(day=1), k * interval)  # 1° del mes, siempre válido
            if end_mode == 'until' and ref is not None and ref > end_date:
                break
            d = _add_months(start_d, k * interval)
            if d is not None and not (end_mode == 'until' and d > end_date):
                out.append(d)
                if _reached_limit():
                    break
            k += 1

    elif freq == 'yearly':
        k = 0
        guard = 0
        while len(out) < cap and guard < cap * 3:
            guard += 1
            yr = start_d.year + k * interval
            if end_mode == 'until' and date(yr, 1, 1) > end_date:
                break
            try:
                d = date(yr, start_d.month, start_d.day)  # 29-feb se omite en años no bisiestos
            except ValueError:
                d = None
            if d is not None and not (end_mode == 'until' and d > end_date):
                out.append(d)
                if _reached_limit():
                    break
            k += 1

    return out[:cap]


# ============================================================
#  APP FACTORY
# ============================================================
def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.from_object(Config)
    app.config['SECRET_KEY'] = app.config['SECRET_KEY'] or 'calendarios-map-secret-key-2024'
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    try:
        app.supabase = SupabaseAPI(app.config['SUPABASE_URL'], app.config['SUPABASE_KEY'])
        print('Supabase OK')
    except Exception as e:
        print(f'Supabase error: {e}'); app.supabase = None

    login_manager.init_app(app)
    login_manager.login_view = 'login'

    @app.context_processor
    def _inject_globals():
        return {'webauthn_available': WEBAUTHN_AVAILABLE,
                'face_login_enabled': True}

    # ------ PWA: service worker / manifest / offline desde la raíz ------
    @app.route('/sw.js')
    def pwa_service_worker():
        resp = send_from_directory(app.static_folder, 'sw.js',
                                   mimetype='application/javascript')
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.route('/manifest.webmanifest')
    def pwa_manifest():
        return send_from_directory(app.static_folder, 'manifest.webmanifest',
                                   mimetype='application/manifest+json')

    @app.route('/offline.html')
    def pwa_offline():
        return send_from_directory(app.static_folder, 'offline.html')

    # ------ Digital Asset Links: vincula la app TWA de Google Play con la web ------
    @app.route('/.well-known/assetlinks.json')
    def well_known_assetlinks():
        return send_from_directory(app.static_folder, 'assetlinks.json',
                                   mimetype='application/json')

    @login_manager.user_loader
    def load_user(uid):
        if app.supabase:
            u = app.supabase.get('users', {'id': uid}, select='id,email,full_name,role,modules')
            if u:
                return User(u[0])
        return None

    # ------ Security headers on every response ------
    @app.after_request
    def add_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        p = request.path
        if p.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        elif p in ('/calendar/api/titles', '/calendar/api/encargados',
                   '/calendar/api/temas', '/calendar/api/ciudades', '/calendar/api/clients'):
            response.headers['Cache-Control'] = 'private, max-age=60'
        elif p.startswith('/calendar/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    # ------ Context processor (cached) ------
    @app.context_processor
    def inject_layout_globals():
        connected = False; needs_reauth = False
        try:
            if current_user.is_authenticated and app.supabase:
                cache_key = f'google_status_{current_user.role}'
                val, hit = _google_cache.get(cache_key)
                if hit:
                    connected, needs_reauth = val
                else:
                    tokens = app.supabase.get('google_tokens',
                        {'email': GOOGLE_ACCOUNT_EMAIL}, select='email,token,refresh_token,token_expiry,id')
                    if tokens:
                        if current_user.role == 'admin':
                            connected = get_google_creds(app) is not None
                            needs_reauth = not connected
                        else:
                            connected = True
                    _google_cache.set(cache_key, (connected, needs_reauth))
        except Exception:
            pass
        return {'google_connected_global': connected, 'google_needs_reauth': needs_reauth}

    # ============================================================
    #  PUBLIC ROUTES
    # ============================================================
    @app.route('/')
    def home():
        return redirect('/dashboard') if current_user.is_authenticated else render_template('index.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect('/dashboard')
        if request.method == 'POST':
            email = _sanitize(request.form.get('email', ''), 254).lower()
            pw = request.form.get('password', '')
            if not email or not pw:
                flash('Completa todos los campos.', 'danger')
                return render_template('login.html')
            users = app.supabase.get('users', {'email': email})
            if users and check_password_hash(users[0]['password_hash'], pw):
                login_user(User(users[0]))
                return redirect(request.args.get('next') or '/dashboard')
            flash('Email o contraseña incorrectos.', 'danger')
        return render_template('login.html')

    # ============================================================
    #  WEBAUTHN — Face ID / huella (passkeys)
    # ============================================================
    def _rp_id():
        return request.host.split(':')[0]

    def _origin():
        return f'{request.scheme}://{request.host}'

    def _wa_guard():
        if not WEBAUTHN_AVAILABLE:
            return jsonify({'error': 'WebAuthn no instalado en el servidor'}), 503
        return None

    @app.route('/webauthn/register/begin', methods=['POST'])
    @login_required
    def webauthn_register_begin():
        guard = _wa_guard()
        if guard:
            return guard
        existing = app.supabase.get('webauthn_credentials',
            {'user_id': str(current_user.id)}, select='credential_id')
        exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c['credential_id']))
                   for c in existing if c.get('credential_id')]
        opts = generate_registration_options(
            rp_id=_rp_id(),
            rp_name='calendarios-map',
            user_id=str(current_user.id).encode('utf-8'),
            user_name=current_user.email or str(current_user.id),
            user_display_name=current_user.full_name or current_user.email or 'Usuario',
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED),
            exclude_credentials=exclude,
        )
        session['wa_reg_challenge'] = bytes_to_base64url(opts.challenge)
        return app.response_class(options_to_json(opts), mimetype='application/json')

    @app.route('/webauthn/register/complete', methods=['POST'])
    @login_required
    def webauthn_register_complete():
        guard = _wa_guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        nombre = (data.pop('nombre', '') or '')[:80]
        challenge = session.pop('wa_reg_challenge', None)
        if not challenge:
            return jsonify({'success': False, 'error': 'Sesion expirada, reintenta'})
        try:
            v = verify_registration_response(
                credential=json.dumps(data),
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=_rp_id(),
                expected_origin=_origin(),
            )
        except Exception as e:
            return jsonify({'success': False, 'error': f'Verificacion fallida: {e}'})
        transports = ','.join((data.get('response', {}) or {}).get('transports', []) or [])
        rec = app.supabase.insert('webauthn_credentials', {
            'user_id': str(current_user.id),
            'credential_id': bytes_to_base64url(v.credential_id),
            'public_key': bytes_to_base64url(v.credential_public_key),
            'sign_count': v.sign_count,
            'transports': transports,
            'nombre': nombre or 'Dispositivo',
        })
        if not rec:
            return jsonify({'success': False,
                            'error': 'No se pudo guardar (¿corriste la migracion 003?)'})
        return jsonify({'success': True})

    @app.route('/webauthn/authenticate/begin', methods=['POST'])
    def webauthn_auth_begin():
        guard = _wa_guard()
        if guard:
            return guard
        opts = generate_authentication_options(
            rp_id=_rp_id(),
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        session['wa_auth_challenge'] = bytes_to_base64url(opts.challenge)
        return app.response_class(options_to_json(opts), mimetype='application/json')

    @app.route('/webauthn/authenticate/complete', methods=['POST'])
    def webauthn_auth_complete():
        guard = _wa_guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        challenge = session.pop('wa_auth_challenge', None)
        if not challenge:
            return jsonify({'success': False, 'error': 'Sesion expirada, reintenta'})
        cred_id = data.get('id', '')
        rows = app.supabase.get('webauthn_credentials', {'credential_id': cred_id})
        if not rows:
            return jsonify({'success': False, 'error': 'Dispositivo no reconocido'})
        rec = rows[0]
        try:
            v = verify_authentication_response(
                credential=json.dumps(data),
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=_rp_id(),
                expected_origin=_origin(),
                credential_public_key=base64url_to_bytes(rec['public_key']),
                credential_current_sign_count=rec.get('sign_count', 0) or 0,
                require_user_verification=False,
            )
        except Exception as e:
            return jsonify({'success': False, 'error': f'Autenticacion fallida: {e}'})
        app.supabase.update('webauthn_credentials', rec['id'], {
            'sign_count': v.new_sign_count,
            'last_used_at': datetime.now(timezone.utc).isoformat(),
        })
        users = app.supabase.get('users', {'id': rec['user_id']},
                                 select='id,email,full_name,role')
        if not users:
            return jsonify({'success': False, 'error': 'Usuario no encontrado'})
        login_user(User(users[0]))
        return jsonify({'success': True, 'redirect': '/dashboard'})

    @app.route('/webauthn/credentials', methods=['GET'])
    @login_required
    def webauthn_credentials_list():
        rows = app.supabase.get('webauthn_credentials',
            {'user_id': str(current_user.id)},
            select='id,nombre,created_at,last_used_at')
        return jsonify(rows or [])

    @app.route('/webauthn/credentials/delete/<cred_pk>', methods=['POST'])
    @login_required
    def webauthn_credentials_delete(cred_pk):
        rows = app.supabase.get('webauthn_credentials', {'id': cred_pk}, select='id,user_id')
        if not rows or str(rows[0].get('user_id')) != str(current_user.id):
            return jsonify({'success': False, 'error': 'No autorizado'})
        app.supabase.delete('webauthn_credentials', cred_pk)
        return jsonify({'success': True})

    # ============================================================
    #  FACE LOGIN — reconocimiento facial por cámara (face-api.js)
    #  Descriptor 128-d calculado en el navegador; comparación en
    #  el servidor. Conveniencia: SIN detección de vida (un foto/
    #  pantalla puede engañarlo). La passkey es más segura.
    # ============================================================
    FACE_THRESHOLD = 0.55   # distancia euclidiana máxima para considerar match

    def _face_distance(a, b):
        if len(a) != len(b):
            return 9.9
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    def _valid_descriptor(d):
        return (isinstance(d, list) and len(d) == 128
                and all(isinstance(x, (int, float)) for x in d))

    @app.route('/face/enroll', methods=['POST'])
    @login_required
    def face_enroll():
        data = request.get_json(silent=True) or {}
        desc = data.get('descriptor')
        if not _valid_descriptor(desc):
            return jsonify({'success': False, 'error': 'Descriptor facial invalido'})
        nombre = (data.get('nombre') or 'Rostro')[:80]
        rec = app.supabase.insert('face_descriptors', {
            'user_id': str(current_user.id),
            'descriptor': json.dumps(desc),
            'nombre': nombre,
        })
        if not rec:
            return jsonify({'success': False,
                            'error': 'No se pudo guardar (¿corriste la migracion 004?)'})
        return jsonify({'success': True})

    @app.route('/face/list', methods=['GET'])
    @login_required
    def face_list():
        rows = app.supabase.get('face_descriptors', {'user_id': str(current_user.id)},
                                select='id,nombre,created_at')
        return jsonify(rows or [])

    @app.route('/face/delete/<fid>', methods=['POST'])
    @login_required
    def face_delete(fid):
        rows = app.supabase.get('face_descriptors', {'id': fid}, select='id,user_id')
        if not rows or str(rows[0].get('user_id')) != str(current_user.id):
            return jsonify({'success': False, 'error': 'No autorizado'})
        app.supabase.delete('face_descriptors', fid)
        return jsonify({'success': True})

    @app.route('/face/verify', methods=['POST'])
    def face_verify():
        data = request.get_json(silent=True) or {}
        email = _sanitize(data.get('email', ''), 254).lower()
        desc = data.get('descriptor')
        if not email or not _valid_descriptor(desc):
            return jsonify({'success': False, 'error': 'Datos invalidos'})
        users = app.supabase.get('users', {'email': email},
                                 select='id,email,full_name,role')
        # Mensaje genérico para no revelar si el email existe
        if not users:
            return jsonify({'success': False, 'error': 'Rostro no reconocido'})
        uid = str(users[0]['id'])
        stored = app.supabase.get('face_descriptors', {'user_id': uid}, select='descriptor')
        best = 9.9
        for s in (stored or []):
            try:
                v = json.loads(s['descriptor'])
            except Exception:
                continue
            if _valid_descriptor(v):
                best = min(best, _face_distance(desc, v))
        if best <= FACE_THRESHOLD:
            login_user(User(users[0]))
            return jsonify({'success': True, 'redirect': '/dashboard'})
        return jsonify({'success': False, 'error': 'Rostro no reconocido'})

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect('/dashboard')
        if request.method == 'POST':
            email = _sanitize(request.form.get('email', ''), 254).lower()
            password = request.form.get('password', '')
            name = _sanitize(request.form.get('full_name', ''), 100)
            cals = request.form.getlist('calendars')
            if not email or not password or not name:
                flash('Completa todos los campos.', 'danger')
                return render_template('register.html', calendarios=_get_calendar_config(app))
            if len(password) < 6:
                flash('La contrasena debe tener al menos 6 caracteres.', 'warning')
                return render_template('register.html', calendarios=_get_calendar_config(app))
            if app.supabase.get('users', {'email': email}):
                flash('Este email ya esta registrado.', 'warning')
                return render_template('register.html', calendarios=_get_calendar_config(app))
            result = app.supabase.insert('users', {
                'email': email, 'password_hash': generate_password_hash(password),
                'full_name': name, 'role': 'staff'})
            if result:
                uid = result[0]['id']
                for cal_id in cals:
                    app.supabase.insert('calendar_permissions',
                        {'user_id': uid, 'calendar_id': cal_id, 'status': 'pending'})
                flash('Solicitud enviada. Espera aprobacion del administrador.', 'success')
                return redirect('/login')
            flash('Error al registrar. Intentalo de nuevo.', 'danger')
        return render_template('register.html', calendarios=_get_calendar_config(app))

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect('/')

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        if request.method == 'POST':
            data = {}
            name = _sanitize(request.form.get('full_name', ''), 100)
            email = _sanitize(request.form.get('email', ''), 254).lower()
            pw = request.form.get('password', '')
            if name: data['full_name'] = name
            if email and _validate_email(email): data['email'] = email
            if pw:
                if len(pw) < 6:
                    flash('La contrasena debe tener al menos 6 caracteres.', 'warning')
                    return redirect('/profile')
                data['password_hash'] = generate_password_hash(pw)
            if data:
                app.supabase.update('users', current_user.id, data)
            flash('Datos actualizados', 'success')
            return redirect('/profile')
        my_cals = get_user_calendars(app, current_user.id)
        return render_template('profile.html', my_calendars=my_cals, all_modules=ALL_MODULES)

    # ============================================================
    #  MICROSOFT OAUTH — To-Do
    # ============================================================
    @app.route('/auth/microsoft')
    @login_required
    def auth_microsoft():
        if not is_admin(): return redirect(url_for('planning'))
        cid = app.config.get('MS_CLIENT_ID', '')
        if not cid:
            flash('Configura MS_CLIENT_ID en las variables de entorno.', 'warning')
            return redirect(url_for('planning'))
        redirect_uri = app.config.get('MS_REDIRECT_URI') or request.host_url.rstrip('/') + '/auth/microsoft/callback'
        params = (f'?client_id={cid}'
                  f'&response_type=code'
                  f'&redirect_uri={_url_quote(redirect_uri, safe="")}'
                  f'&scope={_url_quote(MS_SCOPES, safe="")}'
                  f'&response_mode=query'
                  f'&prompt=select_account')
        return redirect(MS_AUTH_URL + params)

    @app.route('/auth/microsoft/callback')
    @login_required
    def auth_microsoft_callback():
        if not is_admin(): return redirect(url_for('planning'))
        code  = request.args.get('code')
        error = request.args.get('error_description') or request.args.get('error')
        if error:
            flash(f'Microsoft error: {error}', 'danger')
            return redirect(url_for('planning'))
        if not code:
            flash('No se recibió código de autorización.', 'danger')
            return redirect(url_for('planning'))
        redirect_uri = app.config.get('MS_REDIRECT_URI') or request.host_url.rstrip('/') + '/auth/microsoft/callback'
        try:
            r = req_lib.post(MS_TOKEN_URL, data={
                'client_id':     app.config.get('MS_CLIENT_ID', ''),
                'client_secret': app.config.get('MS_CLIENT_SECRET', ''),
                'grant_type':    'authorization_code',
                'code':          code,
                'redirect_uri':  redirect_uri,
                'scope':         MS_SCOPES,
            }, timeout=(5, 15))
            if r.status_code != 200:
                flash(f'Error al obtener token: {r.text[:200]}', 'danger')
                return redirect(url_for('planning'))
            d = r.json()
            exp = (datetime.now(timezone.utc)
                   + timedelta(seconds=d.get('expires_in', 3600))).isoformat()
            # Get user email from Graph
            me_r = req_lib.get(f'{MS_GRAPH_URL}/me',
                               headers={'Authorization': f'Bearer {d["access_token"]}'},
                               timeout=(5, 10))
            ms_email = me_r.json().get('mail') or me_r.json().get('userPrincipalName', 'microsoft') if me_r.ok else 'microsoft'
            # Upsert token
            existing = app.supabase.get('ms_tokens', {'email': ms_email})
            token_data = {
                'email':         ms_email,
                'access_token':  d['access_token'],
                'refresh_token': d.get('refresh_token', ''),
                'expires_at':    exp,
            }
            if existing:
                app.supabase.update('ms_tokens', existing[0]['id'], token_data)
            else:
                app.supabase.insert('ms_tokens', token_data)
            flash(f'✅ Microsoft To-Do conectado ({ms_email})', 'success')
        except Exception as e:
            flash(f'Error de conexión: {e}', 'danger')
        return redirect(url_for('planning'))

    @app.route('/auth/microsoft/disconnect', methods=['POST'])
    @login_required
    def auth_microsoft_disconnect():
        if not is_admin(): return jsonify({'success': False})
        tokens = app.supabase.get('ms_tokens', select='id')
        for t in (tokens or []):
            app.supabase.delete('ms_tokens', t['id'])
        return jsonify({'success': True})

    # ============================================================
    #  GOOGLE OAUTH
    # ============================================================
    @app.route('/auth/google')
    @login_required
    def google_auth():
        if not is_admin():
            flash('Solo el administrador puede conectar Google Calendar.', 'warning')
            return redirect('/dashboard')
        flow = Flow.from_client_config({'web': {
            'client_id': app.config['GOOGLE_CLIENT_ID'],
            'client_secret': app.config['GOOGLE_CLIENT_SECRET'],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': [app.config['GOOGLE_REDIRECT_URI']]}}, scopes=GOOGLE_SCOPES)
        flow.redirect_uri = app.config['GOOGLE_REDIRECT_URI']
        auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
        session['state'] = state
        return redirect(auth_url)

    @app.route('/auth/google/callback')
    @login_required
    def google_callback():
        state = session.get('state')
        if not state:
            flash('Sesion expirada. Intenta de nuevo.', 'warning')
            return redirect('/dashboard')
        flow = Flow.from_client_config({'web': {
            'client_id': app.config['GOOGLE_CLIENT_ID'],
            'client_secret': app.config['GOOGLE_CLIENT_SECRET'],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': [app.config['GOOGLE_REDIRECT_URI']]}},
            scopes=GOOGLE_SCOPES, state=state)
        flow.redirect_uri = app.config['GOOGLE_REDIRECT_URI']
        flow.fetch_token(authorization_response=request.url)
        save_google_creds(app, flow.credentials)
        flash('Google Calendar conectado correctamente.', 'success')
        return redirect('/dashboard')

    # ============================================================
    #  ADMIN — USERS  (optimized: O(3) queries instead of O(N+3))
    # ============================================================
    @app.route('/admin/users')
    @login_required
    def admin_users():
        if not is_admin():
            return redirect('/dashboard')
        # 3 queries total (users, calendar_config, all permissions)
        users     = app.supabase.get('users', select='id,email,full_name,role,modules,created_at')
        all_cals  = _get_calendar_config(app)
        all_perms = app.supabase.get('calendar_permissions',
                        select='id,user_id,calendar_id,status')
        cal_by_id = {c['calendar_id']: c for c in all_cals}
        # Group permissions in Python
        approved_by_user = defaultdict(list)
        pending_perms    = defaultdict(list)
        for p in all_perms:
            if p['status'] == 'approved':
                approved_by_user[p['user_id']].append(p['calendar_id'])
            elif p['status'] == 'pending':
                pending_perms[p['user_id']].append(p)
        # Attach calendars to each user
        for u in users:
            u['calendars'] = [cal_by_id[cid] for cid in approved_by_user.get(u['id'], [])
                              if cid in cal_by_id]
        user_by_id = {u['id']: u for u in users}
        # Build pending list
        pending = []; pending_all = []
        for uid, perms_list in pending_perms.items():
            for p in perms_list:
                pending_all.append({'id': p['id'], 'user_id': uid, 'calendar_id': p['calendar_id']})
            u = user_by_id.get(uid, {})
            pending.append({
                'user_id': uid,
                'user_name':  u.get('full_name', ''),
                'user_email': u.get('email', ''),
                'calendars':  [cal_by_id[p['calendar_id']] for p in perms_list
                               if p['calendar_id'] in cal_by_id],
            })
        # Cuentas MS conectadas + permisos por usuario + conteos
        ms_accounts_raw = app.supabase.get('ms_tokens', select='email') or []
        ms_accounts = [t.get('email','') for t in ms_accounts_raw if t.get('email')]
        # Contar tareas por cuenta para mostrar al admin la magnitud
        ms_counts = {}
        for ms in ms_accounts:
            rows = app.supabase.get('tasks', {'ms_email': ms, 'source': 'ms_todo'},
                                    select='id') or []
            ms_counts[ms] = len(rows)
        ms_perms_all = app.supabase.get('ms_account_permissions', select='user_id,ms_email') or []
        ms_by_user = defaultdict(list)
        for p in ms_perms_all:
            ms_by_user[p['user_id']].append(p['ms_email'])
        for u in users:
            u['ms_emails'] = ms_by_user.get(u['id'], [])
        return render_template('admin_users.html', users=users, calendarios=all_cals,
                               pending=pending, pending_all=pending_all,
                               all_modules=ALL_MODULES, ms_accounts=ms_accounts,
                               ms_counts=ms_counts)

    # ============================================================
    #  ADMIN — DATABASE
    # ============================================================
    @app.route('/admin/database')
    @login_required
    def admin_database():
        if not is_admin():
            return redirect('/dashboard')
        return render_template('admin_database.html',
            users        = app.supabase.get('users', select='id,email,full_name,role,created_at'),
            ciudades     = app.supabase.get('ciudades'),
            titles       = app.supabase.get('appointment_titles'),
            encargados   = app.supabase.get('encargados'),
            clients      = app.supabase.get('clients'),
            appointments = app.supabase.get('appointments'),
            calendarios  = _get_calendar_config(app))

    @app.route('/admin/database/update', methods=['POST'])
    @login_required
    def admin_db_update():
        if not is_admin(): return jsonify({'success': False})
        table = request.form.get('table'); record_id = request.form.get('id')
        data = {k: v for k, v in request.form.items() if k not in ['table', 'id']}
        if data: app.supabase.update(table, record_id, data)
        if table == 'calendar_config':
            _cal_cache.invalidate('all')
            _user_cal_cache.invalidate_prefix('')
        flash('Registro actualizado', 'success')
        return redirect('/admin/database')

    @app.route('/admin/database/delete', methods=['POST'])
    @login_required
    def admin_db_delete():
        if not is_admin(): return jsonify({'success': False})
        table = request.form.get('table')
        app.supabase.delete(table, request.form.get('id'))
        if table == 'calendar_config':
            _cal_cache.invalidate('all')
            _user_cal_cache.invalidate_prefix('')
        flash('Registro eliminado', 'success')
        return redirect('/admin/database')

    @app.route('/admin/database/insert', methods=['POST'])
    @login_required
    def admin_db_insert():
        if not is_admin(): return jsonify({'success': False})
        table = request.form.get('table')
        data = {k: v for k, v in request.form.items() if k not in ['table']}
        if data: app.supabase.insert(table, data)
        if table == 'calendar_config':
            _cal_cache.invalidate('all')
            _user_cal_cache.invalidate_prefix('')
        flash('Registro creado', 'success')
        return redirect('/admin/database')

    @app.route('/admin/user/update/<uid>', methods=['POST'])
    @login_required
    def admin_update_user(uid):
        if not is_admin(): return redirect('/dashboard')
        data = {}
        if request.form.get('full_name'):
            data['full_name'] = _sanitize(request.form.get('full_name'), 100)
        if request.form.get('email'):
            data['email'] = _sanitize(request.form.get('email'), 254).lower()
        if request.form.get('password'):
            data['password_hash'] = generate_password_hash(request.form.get('password'))
        if request.form.get('role'):
            data['role'] = request.form.get('role')
        if data: app.supabase.update('users', uid, data)
        # Módulos
        mod_ids = request.form.getlist('modules')
        app.supabase.update('users', uid, {'modules': ','.join(mod_ids)})
        # Calendarios
        cal_ids = request.form.getlist('calendars')
        for p in app.supabase.get('calendar_permissions', {'user_id': uid}, select='id'):
            app.supabase.delete('calendar_permissions', p['id'])
        for cal_id in cal_ids:
            app.supabase.insert('calendar_permissions',
                {'user_id': uid, 'calendar_id': cal_id, 'status': 'approved'})
        # Cuentas Microsoft autorizadas
        ms_emails = request.form.getlist('ms_emails')
        for p in app.supabase.get('ms_account_permissions', {'user_id': uid}, select='id'):
            app.supabase.delete('ms_account_permissions', p['id'])
        for ms_email in ms_emails:
            app.supabase.insert('ms_account_permissions',
                {'user_id': uid, 'ms_email': ms_email})
        _user_cal_cache.invalidate(uid)
        flash('Usuario actualizado', 'success')
        return redirect('/admin/users')

    @app.route('/admin/user/delete/<uid>', methods=['POST'])
    @login_required
    def admin_delete_user(uid):
        if not is_admin(): return jsonify({'success': False, 'error': 'Sin autorización'})
        if uid == str(current_user.id):
            return jsonify({'success': False, 'error': 'No puedes eliminarte a ti mismo'})
        # Borrar todos los registros relacionados antes de eliminar el usuario
        for tbl in ['calendar_permissions', 'webauthn_credentials', 'face_descriptors',
                    'ms_account_permissions']:
            for row in app.supabase.get(tbl, {'user_id': uid}, select='id'):
                app.supabase.delete(tbl, row['id'])
        ok = app.supabase.delete('users', uid)
        _user_cal_cache.invalidate(uid)
        return jsonify({'success': ok, 'error': None if ok else 'No se pudo eliminar el usuario'})

    @app.route('/admin/approve-one/<pid>', methods=['POST'])
    @login_required
    def admin_approve_one(pid):
        if not is_admin(): return jsonify({'success': False})
        app.supabase.update('calendar_permissions', pid, {'status': 'approved'})
        _user_cal_cache.invalidate_prefix('')  # any user might be affected
        return jsonify({'success': True})

    @app.route('/admin/reject-one/<pid>', methods=['POST'])
    @login_required
    def admin_reject_one(pid):
        if not is_admin(): return jsonify({'success': False})
        app.supabase.update('calendar_permissions', pid, {'status': 'rejected'})
        _user_cal_cache.invalidate_prefix('')
        return jsonify({'success': True})

    @app.route('/admin/approve-all/<uid>', methods=['POST'])
    @login_required
    def admin_approve_all(uid):
        if not is_admin(): return jsonify({'success': False})
        for p in app.supabase.get('calendar_permissions',
                {'user_id': uid, 'status': 'pending'}, select='id'):
            app.supabase.update('calendar_permissions', p['id'], {'status': 'approved'})
        _user_cal_cache.invalidate(uid)
        return jsonify({'success': True})

    @app.route('/admin/reject-all/<uid>', methods=['POST'])
    @login_required
    def admin_reject_all(uid):
        if not is_admin(): return jsonify({'success': False})
        for p in app.supabase.get('calendar_permissions',
                {'user_id': uid, 'status': 'pending'}, select='id'):
            app.supabase.update('calendar_permissions', p['id'], {'status': 'rejected'})
        _user_cal_cache.invalidate(uid)
        return jsonify({'success': True})

    # ============================================================
    #  DASHBOARD  (optimized: O(3) queries instead of O(2N+3))
    # ============================================================
    def _dashboard_widgets(app):
        """Calcula las cifras-tarjeta del panel para el usuario logueado."""
        today_iso = date.today().isoformat()
        in_7d_iso = (date.today() + timedelta(days=7)).isoformat()
        # Tareas: aplica los mismos permisos que /planning/api/tasks
        all_tasks = app.supabase.get('tasks', select='id,status,due_date,priority,source,ms_email,created_by,assigned_to,assigned_email,subtasks') or []
        if not is_admin():
            allowed_ms = set(get_user_ms_emails(app, current_user.id))
            has_todo   = 'todo'     in getattr(current_user, 'modules', [])
            has_plan   = 'planning' in getattr(current_user, 'modules', [])
            uid = str(current_user.id)
            def vis(t):
                if t.get('source') == 'ms_todo':
                    return has_todo and (t.get('ms_email') or '') in allowed_ms
                if not has_plan: return False
                if t.get('created_by') == uid: return True
                if t.get('assigned_to') == uid: return True
                if (t.get('assigned_email') or '').lower() == (current_user.email or '').lower():
                    return True
                return False
            all_tasks = [t for t in all_tasks if vis(t)]
        pending_all   = [t for t in all_tasks if t.get('status') != 'done']
        overdue       = [t for t in pending_all if t.get('due_date') and t['due_date'] < today_iso]
        today_tasks   = [t for t in all_tasks  if t.get('due_date') == today_iso]
        week_tasks    = [t for t in pending_all if t.get('due_date') and today_iso <= t['due_date'] <= in_7d_iso]
        manual_pend   = [t for t in pending_all if t.get('source') != 'ms_todo']
        todo_pend     = [t for t in pending_all if t.get('source') == 'ms_todo']
        # Subtareas pendientes (suma global)
        sub_pending = 0
        for t in pending_all:
            for s in (t.get('subtasks') or []):
                if not s.get('done'): sub_pending += 1
        # Citas próximas (siguientes 7 días)
        next_apts = []
        try:
            apt_rows = app.supabase.get('appointments', select='id,title,start_time,status,encargado,calendar_id') or []
            # Filtra por permisos de calendarios del usuario si no es admin
            if not is_admin():
                user_cal_ids = {c.get('calendar_id') for c in get_user_calendars(app, current_user.id)}
                apt_rows = [a for a in apt_rows if a.get('calendar_id') in user_cal_ids]
            for a in apt_rows:
                st = (a.get('start_time') or '')[:10]
                if st and today_iso <= st <= in_7d_iso and a.get('status') != 'cancelled':
                    next_apts.append(a)
            next_apts.sort(key=lambda x: x.get('start_time') or '')
            next_apts = next_apts[:5]
        except Exception:
            next_apts = []
        # Cuentas MS conectadas (solo admin)
        ms_accounts = []
        if is_admin():
            ms_accounts = [t.get('email','') for t in (app.supabase.get('ms_tokens', select='email') or []) if t.get('email')]
        return {
            'total_pending':  len(pending_all),
            'overdue':        len(overdue),
            'today_count':    len(today_tasks),
            'week_count':     len(week_tasks),
            'manual_pending': len(manual_pend),
            'todo_pending':   len(todo_pend),
            'sub_pending':    sub_pending,
            'next_apts':      next_apts,
            'ms_accounts':    ms_accounts,
        }

    @app.route('/dashboard')
    @login_required
    def dashboard():
        if is_admin():
            all_cals   = _get_calendar_config(app)
            all_pending = app.supabase.get('calendar_permissions',
                {'status': 'pending'}, select='id,user_id,calendar_id')
            # Fetch only the users that appear in pending list (single IN query)
            pending_uids = list({p['user_id'] for p in all_pending})
            if pending_uids:
                pend_users = app.supabase.get_in('users', 'id', pending_uids,
                    select='id,full_name,email')
                user_by_id = {u['id']: u for u in pend_users}
            else:
                user_by_id = {}
            cal_by_id   = {c['calendar_id']: c for c in all_cals}
            by_user     = defaultdict(list)
            pending_all = []
            for p in all_pending:
                pending_all.append({'id': p['id'], 'user_id': p['user_id'],
                                    'calendar_id': p['calendar_id']})
                by_user[p['user_id']].append(p)
            pending = []
            for uid, perms_list in by_user.items():
                u = user_by_id.get(uid, {})
                pending.append({
                    'user_id':    uid,
                    'user_name':  u.get('full_name', ''),
                    'user_email': u.get('email', ''),
                    'calendars':  [cal_by_id[p['calendar_id']] for p in perms_list
                                   if p['calendar_id'] in cal_by_id],
                })
            cals = all_cals
        else:
            cals = get_user_calendars(app, current_user.id)
            pending = []; pending_all = []
        google_ok = get_google_creds(app) is not None
        widgets = _dashboard_widgets(app)
        return render_template('dashboard.html', calendarios=cals, pending=pending,
                               pending_all=pending_all, google_connected=google_ok,
                               widgets=widgets,
                               can_planning=user_can('planning'),
                               can_todo=user_can('todo'),
                               can_calendar=user_can('calendar'))

    # ============================================================
    #  CALENDAR VIEW
    # ============================================================
    @app.route('/calendar')
    @login_required
    def calendar():
        if not user_can('calendar'):
            flash('No tienes acceso al módulo Calendario.', 'warning')
            return redirect('/dashboard')
        cals = (_get_calendar_config(app) if is_admin()
                else get_user_calendars(app, current_user.id))
        return render_template('calendar.html', calendarios=cals,
                               google_connected=get_google_creds(app) is not None)

    # ============================================================
    #  API — EVENTS  (single query with IN filter for non-admin)
    # ============================================================
    APPT_SELECT_BASE = ('id,title,encargado,start_time,end_time,status,calendar_id,'
                        'tema,client_name,client_email,notes,lugar,direccion,mapa,'
                        'ciudad,meeting_link,google_event_id')
    # Columnas de recurrencia — requieren la migración 002. El SELECT cae al
    # base automáticamente si todavía no existen (ver _events_query abajo).
    APPT_SELECT = APPT_SELECT_BASE + ',is_recurring,parent_event_id'

    @app.route('/calendar/api/events')
    @login_required
    def api_events():
        def _events_query(fetch):
            # fetch(select) -> lista. Intenta con columnas de recurrencia;
            # si vuelve vacío (p.ej. columna inexistente antes de migrar),
            # reintenta con el SELECT base para no ocultar los eventos.
            rows = fetch(APPT_SELECT)
            if not rows:
                rows = fetch(APPT_SELECT_BASE)
            return rows

        if is_admin():
            events = _events_query(
                lambda sel: app.supabase.get('appointments', select=sel))
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            if not ucal:
                return jsonify([])
            # One query with IN — replaces N separate queries
            events = _events_query(
                lambda sel: app.supabase.get_in('appointments', 'calendar_id', ucal, select=sel))
        colors = {'pending': '#f59e0b', 'confirmed': '#10b981', 'cancelled': '#ef4444'}
        result = []
        for e in events:
            is_rec = e.get('is_recurring', False)
            result.append({
                'id': e['id'],
                'title': f"{'R ' if is_rec else ''}{e['title']} — {e.get('encargado', '')}",
                'start': e['start_time'], 'end': e['end_time'],
                'backgroundColor': colors.get(e.get('status'), '#3b82f6'),
                'borderColor':     colors.get(e.get('status'), '#3b82f6'),
                'extendedProps': {
                    'title': e.get('title', ''), 'encargado': e.get('encargado', ''),
                    'tema': e.get('tema', ''), 'client_name': e.get('client_name', ''),
                    'client_email': e.get('client_email', ''),
                    'status': e.get('status', 'pending'),
                    'calendar_id': e.get('calendar_id', ''),
                    'notes': e.get('notes', ''), 'lugar': e.get('lugar', ''),
                    'direccion': e.get('direccion', ''), 'mapa': e.get('mapa', ''),
                    'ciudad': e.get('ciudad', ''), 'meeting_link': e.get('meeting_link', ''),
                    'google_event_id': e.get('google_event_id', ''),
                    'is_recurring': is_rec,
                    'parent_event_id': e.get('parent_event_id', ''), 'id': e['id'],
                },
            })
        return jsonify(result)

    # ============================================================
    #  API — LOOKUP DATA (projected, browser-cached 60s)
    # ============================================================
    @app.route('/calendar/api/titles')
    @login_required
    def api_titles():
        return jsonify([t['title'] for t in
            app.supabase.get('appointment_titles', select='title')])

    @app.route('/calendar/api/encargados')
    @login_required
    def api_encargados():
        return jsonify([e['name'] for e in
            app.supabase.get('encargados', select='name')])

    @app.route('/calendar/api/temas')
    @login_required
    def api_temas():
        return jsonify([t['description'] for t in
            app.supabase.get('temas', select='description')])

    @app.route('/calendar/api/clients')
    @login_required
    def api_clients():
        return jsonify([{'name': c['name'], 'email': c.get('email', '')} for c in
            app.supabase.get('clients', select='name,email')])

    @app.route('/calendar/api/ciudades')
    @login_required
    def api_ciudades():
        return jsonify([c['name'] for c in
            app.supabase.get('ciudades', select='name')])

    # ============================================================
    #  API — PENDING
    # ============================================================
    @app.route('/calendar/api/pending')
    @login_required
    def api_pending():
        if is_admin():
            pending = [a for a in
                app.supabase.get('appointments',
                    {'status': 'pending'},
                    select='id,title,encargado,tema,client_name,start_time')
            ]
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            if not ucal:
                return jsonify([])
            all_p = app.supabase.get_in('appointments', 'calendar_id', ucal,
                select='id,title,encargado,tema,client_name,start_time,calendar_id,status')
            pending = [a for a in all_p if a.get('status') == 'pending']
        return jsonify([{
            'id': a['id'], 'title': a['title'], 'encargado': a.get('encargado', ''),
            'tema': a.get('tema', ''), 'client_name': a.get('client_name', ''),
            'date': a['start_time'].split('T')[0],
            'time': a['start_time'].split('T')[1][:5],
            'is_recurring': a.get('is_recurring', False),
        } for a in pending])

    # ============================================================
    #  API — BOOK  (optimized: insert_ignore replaces check+insert)
    # ============================================================
    @app.route('/calendar/api/book', methods=['POST'])
    @login_required
    def api_book():
        try:
            date_str = request.form.get('date', '').strip()
            time_str = request.form.get('time', '').strip()
            dur_sel  = request.form.get('duration', '30')
            dur      = max(15, min(1440, int(request.form.get('custom_duration', dur_sel) or 30)))

            title      = _sanitize(request.form.get('title', ''), 200).upper()
            cal_id     = _sanitize(request.form.get('calendar_id', ''), 100)
            encargado  = _sanitize(request.form.get('encargado', ''), 100).upper()
            tema       = _sanitize(request.form.get('tema', ''), 300)
            client_name  = _sanitize(request.form.get('client_name', ''), 150).upper()
            client_email = _sanitize(request.form.get('client_email', ''), 254).lower()
            notificar    = [e.strip() for e in request.form.getlist('notificar') if e.strip()]
            tipo      = request.form.get('type', 'presencial')
            lugar     = _sanitize(request.form.get('lugar', ''), 150).upper()
            direccion = _sanitize(request.form.get('direccion', ''), 300)
            mapa      = _sanitize(request.form.get('mapa', ''), 500)
            ciudad    = _sanitize(request.form.get('ciudad', 'CUENCA'), 100).upper()
            link      = _sanitize(request.form.get('meeting_link', ''), 500)
            notes     = _sanitize(request.form.get('notes', ''), 1000)

            sessions_present = bool(request.form.get('sessions', '').strip())
            if not all([title, cal_id, encargado, tema]) or \
               (not sessions_present and not all([date_str, time_str])):
                return jsonify({'success': False, 'error': 'Faltan campos obligatorios'})
            if not is_admin() and not user_has_calendar_access(app, current_user.id, cal_id):
                return jsonify({'success': False, 'error': 'Sin autorizacion para este calendario'})

            # Upsert lookup tables — insert_ignore skips if already exists
            if ciudad:     app.supabase.insert_ignore('ciudades', {'name': ciudad})
            if title:      app.supabase.insert_ignore('appointment_titles', {'title': title})
            if encargado:  app.supabase.insert_ignore('encargados', {'name': encargado})
            if tema:       app.supabase.insert_ignore('temas', {'description': tema})
            if client_name:
                app.supabase.insert_ignore('clients',
                    {'name': client_name, 'email': client_email, 'created_by': current_user.id})

            # ---- Recurring (flexible: daily/weekly/monthly/yearly) ----
            is_recurring = request.form.get('is_recurring') == 'true'
            if is_recurring:
                freq     = request.form.get('rec_freq', 'weekly')
                end_mode = request.form.get('rec_end_mode', 'until')
                try:
                    interval  = max(1, min(366, int(request.form.get('rec_interval', '1') or 1)))
                    start_d   = datetime.strptime(date_str, '%Y-%m-%d').date()
                    weekdays  = json.loads(request.form.get('rec_weekdays', '[]') or '[]')
                    rec_count = max(1, min(REC_HARD_CAP, int(request.form.get('rec_count', '1') or 1)))
                    rec_end   = None
                    if end_mode == 'until':
                        rec_end = datetime.strptime(
                            request.form.get('rec_end_date', ''), '%Y-%m-%d').date()
                except Exception as ex:
                    return jsonify({'success': False, 'error': f'Datos de recurrencia invalidos: {ex}'})

                if freq not in ('daily', 'weekly', 'monthly', 'yearly'):
                    return jsonify({'success': False, 'error': 'Frecuencia invalida'})
                if end_mode == 'until' and (rec_end is None or rec_end < start_d):
                    return jsonify({'success': False, 'error': 'Fecha fin debe ser posterior a inicio'})
                if freq == 'weekly' and weekdays and any(w < 0 or w > 6 for w in weekdays):
                    return jsonify({'success': False, 'error': 'Dias de semana invalidos'})

                dates_to_create = _generate_recurrence_dates(
                    start_d, freq, interval, weekdays, end_mode, rec_end, rec_count)
                if not dates_to_create:
                    return jsonify({'success': False, 'error': 'La recurrencia no genera ninguna fecha'})

                rule_json = json.dumps({
                    'freq': freq, 'interval': interval, 'weekdays': weekdays,
                    'end_mode': end_mode,
                    'end_date': rec_end.isoformat() if rec_end else None,
                    'count': rec_count,
                }, ensure_ascii=False)
                # Aviso si se topó el límite de materialización (recurrencia muy larga/indefinida)
                capped = len(dates_to_create) >= REC_HARD_CAP
                rec_notes = f'[SERIE {len(dates_to_create)} eventos] {notes}'.strip()

                created_ids = []; parent_id = None
                for d in dates_to_create:
                    local_dt = TIMEZONE.localize(
                        datetime.strptime(f'{d.isoformat()} {time_str}:00', '%Y-%m-%d %H:%M:%S'))
                    s_dt = local_dt.astimezone(pytz.UTC)
                    e_dt = s_dt + timedelta(minutes=dur)
                    record = _build_appointment(title, cal_id, encargado, tema,
                        client_name, client_email, s_dt, e_dt, tipo, link,
                        lugar, direccion, mapa, ciudad, notificar, rec_notes, current_user.id)
                    record['is_recurring'] = True
                    if parent_id:
                        record['parent_event_id'] = parent_id
                    else:
                        record['recurrence_rule'] = rule_json
                        if rec_end:
                            record['recurrence_end_date'] = rec_end.isoformat()
                    r = app.supabase.insert('appointments', record)
                    if not r:
                        # Fallback si aún no se corrió la migración de columnas de recurrencia
                        for col in ('is_recurring', 'parent_event_id',
                                    'recurrence_rule', 'recurrence_end_date'):
                            record.pop(col, None)
                        r = app.supabase.insert('appointments', record)
                    if r:
                        aid = r[0]['id']
                        created_ids.append(aid)
                        if parent_id is None:
                            parent_id = aid
                            try:
                                app.supabase.update('appointments', aid, {'parent_event_id': aid})
                            except Exception:
                                pass
                if created_ids:
                    return jsonify({'success': True, 'count': len(created_ids),
                                    'recurring': True, 'capped': capped})
                return jsonify({'success': False, 'error': 'No se pudieron crear los eventos'})

            # ---- Varias fechas / sesiones manuales (distinta hora y duración) ----
            if sessions_present:
                try:
                    sessions = json.loads(request.form.get('sessions', '[]'))
                except Exception:
                    return jsonify({'success': False, 'error': 'Sesiones invalidas'})
                if not isinstance(sessions, list) or not sessions:
                    return jsonify({'success': False, 'error': 'Agrega al menos una fecha'})
                if len(sessions) > 60:
                    return jsonify({'success': False, 'error': 'Maximo 60 sesiones por serie'})

                parsed = []
                for s in sessions:
                    try:
                        sd = datetime.strptime((s.get('date') or ''), '%Y-%m-%d').date()
                        st = (s.get('time') or '')
                        sdur = max(15, min(1440, int(s.get('duration', 60) or 60)))
                        local_dt = TIMEZONE.localize(datetime.strptime(
                            f'{sd.isoformat()} {st}:00', '%Y-%m-%d %H:%M:%S'))
                    except Exception:
                        return jsonify({'success': False,
                                        'error': 'Fecha u hora invalida en una sesion'})
                    parsed.append((local_dt, sdur))
                parsed.sort(key=lambda x: x[0])

                ses_notes = f'[SERIE {len(parsed)} sesiones] {notes}'.strip()
                created_ids = []; parent_id = None
                for local_dt, sdur in parsed:
                    s_dt = local_dt.astimezone(pytz.UTC)
                    e_dt = s_dt + timedelta(minutes=sdur)
                    record = _build_appointment(title, cal_id, encargado, tema,
                        client_name, client_email, s_dt, e_dt, tipo, link,
                        lugar, direccion, mapa, ciudad, notificar, ses_notes, current_user.id)
                    record['is_recurring'] = True
                    if parent_id:
                        record['parent_event_id'] = parent_id
                    r = app.supabase.insert('appointments', record)
                    if not r:
                        for col in ('is_recurring', 'parent_event_id'):
                            record.pop(col, None)
                        r = app.supabase.insert('appointments', record)
                    if r:
                        aid = r[0]['id']; created_ids.append(aid)
                        if parent_id is None:
                            parent_id = aid
                            try:
                                app.supabase.update('appointments', aid, {'parent_event_id': aid})
                            except Exception:
                                pass
                if created_ids:
                    return jsonify({'success': True, 'count': len(created_ids), 'recurring': True})
                return jsonify({'success': False, 'error': 'No se pudieron crear las sesiones'})

            # ---- Single event ----
            local_dt = TIMEZONE.localize(
                datetime.strptime(f'{date_str} {time_str}:00', '%Y-%m-%d %H:%M:%S'))
            s_dt = local_dt.astimezone(pytz.UTC)
            e_dt = s_dt + timedelta(minutes=dur)
            record = _build_appointment(title, cal_id, encargado, tema,
                client_name, client_email, s_dt, e_dt, tipo, link,
                lugar, direccion, mapa, ciudad, notificar, notes, current_user.id)
            result = app.supabase.insert('appointments', record)
            if result:
                return jsonify({'success': True, 'id': result[0]['id']})
            return jsonify({'success': False, 'error': 'Error en base de datos'})

        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)})

    # ============================================================
    #  API — APPROVE / REJECT / DELETE / DELETE-SERIES / SYNC
    # ============================================================
    @app.route('/calendar/api/approve/<aid>', methods=['POST'])
    @login_required
    def api_approve(aid):
        apts = app.supabase.get('appointments', {'id': aid},
            select='id,title,encargado,tema,client_name,client_email,start_time,end_time,'
                   'status,calendar_id,invitados,lugar,direccion,ciudad,mapa,notes,'
                   'meeting_link,google_event_id,google_cal_id')
        if not apts: return jsonify({'success': False})
        apt = apts[0]
        if not is_admin() and not user_has_calendar_access(app, current_user.id, apt.get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})

        # Idempotente: si ya tiene evento en Google, solo confirmar — no duplicar
        if apt.get('google_event_id'):
            app.supabase.update('appointments', aid, {'status': 'confirmed'})
            return jsonify({'success': True, 'message': 'Confirmada (ya sincronizada con Google)'})

        creds = get_google_creds(app)
        if not creds:
            app.supabase.update('appointments', aid, {'status': 'confirmed'})
            return jsonify({'success': True, 'message': 'Aprobada (sin sincronizacion Google)'})
        try:
            service  = build('calendar', 'v3', credentials=creds)
            all_cals = _get_calendar_config(app)
            email_map, gcal_id_map = _make_cal_maps(all_cals)
            cal_id  = apt.get('calendar_id')
            gcal_id = gcal_id_map.get(cal_id, 'primary')
            attendees = _build_attendees(apt, email_map)
            event = _build_google_event(apt, attendees)
            # Buscar si ya existe en Google Calendar para evitar duplicado
            existing = service.events().list(
                calendarId=gcal_id, timeMin=apt['start_time'],
                timeMax=apt['end_time'], q=apt['title'], maxResults=1).execute()
            if existing.get('items'):
                # Ya existe: vincular sin reenviar notificaciones
                gev_id = existing['items'][0]['id']
                app.supabase.update('appointments', aid,
                    {'status': 'confirmed', 'google_event_id': gev_id, 'google_cal_id': gcal_id})
                return jsonify({'success': True, 'message': 'Confirmada (evento ya existía en Google)'})
            # Nuevo evento — notificar a todos los asistentes una sola vez
            created = service.events().insert(
                calendarId=gcal_id, body=event, sendUpdates='all').execute()
            app.supabase.update('appointments', aid,
                {'status': 'confirmed', 'google_event_id': created.get('id'),
                 'google_cal_id': gcal_id})
            return jsonify({'success': True,
                'message': f'Aprobada — {len(attendees)} invitado(s) notificado(s)'})
        except google.auth.exceptions.RefreshError:
            app.supabase.update('appointments', aid, {'status': 'confirmed'})
            return jsonify({'success': True,
                'message': 'Aprobada. Reconecta Google en /auth/google para sincronizar.'})
        except Exception as e:
            if _is_invalid_grant(e):
                app.supabase.update('appointments', aid, {'status': 'confirmed'})
                return jsonify({'success': True, 'message': 'Aprobada. Reconecta Google.'})
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/calendar/api/reject/<aid>', methods=['POST'])
    @login_required
    def api_reject(aid):
        apts = app.supabase.get('appointments', {'id': aid}, select='id,calendar_id')
        if not apts: return jsonify({'success': False})
        if not is_admin() and not user_has_calendar_access(app, current_user.id, apts[0].get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        app.supabase.update('appointments', aid, {'status': 'cancelled'})
        return jsonify({'success': True})

    @app.route('/calendar/api/delete/<aid>', methods=['POST'])
    @login_required
    def api_delete(aid):
        apts = app.supabase.get('appointments', {'id': aid},
            select='id,calendar_id,google_event_id,google_cal_id')
        if not apts: return jsonify({'success': False})
        apt = apts[0]
        if not is_admin() and not user_has_calendar_access(app, current_user.id, apt.get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        if apt.get('google_event_id'):
            creds = get_google_creds(app)
            if creds:
                gcal_id = apt.get('google_cal_id') or 'primary'
                try:
                    build('calendar', 'v3', credentials=creds).events().delete(
                        calendarId=gcal_id, eventId=apt['google_event_id']).execute()
                except Exception:
                    pass
        app.supabase.delete('appointments', aid)
        return jsonify({'success': True})

    @app.route('/calendar/api/delete-series/<parent_id>', methods=['POST'])
    @login_required
    def api_delete_series(parent_id):
        all_apts = app.supabase.get('appointments',
            select='id,calendar_id,google_event_id,google_cal_id,parent_event_id')
        series = [a for a in all_apts
                  if a.get('parent_event_id') == parent_id or a.get('id') == parent_id]
        if not series: return jsonify({'success': False, 'error': 'Serie no encontrada'})
        if not is_admin() and not user_has_calendar_access(
                app, current_user.id, series[0].get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        creds = get_google_creds(app); deleted = 0
        for apt in series:
            if apt.get('google_event_id') and creds:
                gcal_id = apt.get('google_cal_id') or 'primary'
                try:
                    build('calendar', 'v3', credentials=creds).events().delete(
                        calendarId=gcal_id, eventId=apt['google_event_id']).execute()
                except Exception:
                    pass
            app.supabase.delete('appointments', apt['id']); deleted += 1
        return jsonify({'success': True, 'deleted': deleted})

    @app.route('/calendar/api/sync', methods=['POST'])
    @login_required
    def api_sync():
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        creds = get_google_creds(app)
        if not creds: return jsonify({'success': False, 'error': 'Google no conectado'})
        synced = 0; errors = 0; skipped = 0
        all_cals = _get_calendar_config(app)
        email_map, gcal_id_map = _make_cal_maps(all_cals)
        service = build('calendar', 'v3', credentials=creds)
        for apt in app.supabase.get('appointments',
                select='id,title,encargado,tema,client_name,start_time,end_time,calendar_id,'
                       'invitados,direccion,ciudad,lugar,mapa,notes,meeting_link,status,google_event_id'):
            if apt.get('status') != 'confirmed' or apt.get('google_event_id'):
                continue
            try:
                cal_id  = apt.get('calendar_id')
                gcal_id = gcal_id_map.get(cal_id, 'primary')
                existing = service.events().list(
                    calendarId=gcal_id, timeMin=apt['start_time'],
                    timeMax=apt['end_time'], q=apt['title'], maxResults=1).execute()
                if existing.get('items'):
                    app.supabase.update('appointments', apt['id'],
                        {'google_event_id': existing['items'][0]['id'],
                         'google_cal_id': gcal_id})
                    skipped += 1; continue
                attendees = _build_attendees(apt, email_map)
                event = _build_google_event(apt, attendees)
                created = service.events().insert(calendarId=gcal_id,
                    body=event, sendUpdates='all').execute()
                app.supabase.update('appointments', apt['id'],
                    {'google_event_id': created.get('id'), 'google_cal_id': gcal_id})
                synced += 1
            except google.auth.exceptions.RefreshError:
                return jsonify({'success': False, 'synced': synced, 'skipped': skipped,
                    'errors': errors, 'error': 'Google desconectado. Reconecta en /auth/google.'})
            except Exception as e:
                if _is_invalid_grant(e):
                    return jsonify({'success': False, 'synced': synced, 'skipped': skipped,
                        'errors': errors, 'error': 'Google desconectado. Reconecta en /auth/google.'})
                errors += 1
        return jsonify({'success': True, 'synced': synced, 'skipped': skipped, 'errors': errors})

    # ============================================================
    #  RETROACTIVE FIX — patch all existing Google events with
    #  correct location + link (presencial / virtual)
    # ============================================================
    @app.route('/calendar/api/fix-events', methods=['POST'])
    @login_required
    def api_fix_events():
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        creds = get_google_creds(app)
        if not creds: return jsonify({'success': False, 'error': 'Google no conectado'})
        updated = 0; errors = 0; skipped = 0
        try:
            service  = build('calendar', 'v3', credentials=creds)
            all_cals = _get_calendar_config(app)
            email_map, gcal_id_map = _make_cal_maps(all_cals)
            apts = app.supabase.get('appointments',
                select='id,title,encargado,tema,client_name,start_time,end_time,calendar_id,'
                       'invitados,direccion,ciudad,lugar,mapa,notes,meeting_link,status,'
                       'google_event_id,google_cal_id')
            for apt in apts:
                if apt.get('status') != 'confirmed': continue
                gid = apt.get('google_event_id')
                if not gid: skipped += 1; continue
                try:
                    cal_id  = apt.get('calendar_id')
                    gcal_id = apt.get('google_cal_id') or gcal_id_map.get(cal_id, 'primary')
                    attendees = _build_attendees(apt, email_map)
                    ev = _build_google_event(apt, attendees)
                    patch = {'description': ev['description']}
                    if ev.get('location'): patch['location'] = ev['location']
                    service.events().patch(
                        calendarId=gcal_id, eventId=gid, body=patch).execute()
                    updated += 1
                except Exception:
                    errors += 1
            return jsonify({'success': True, 'updated': updated,
                            'skipped': skipped, 'errors': errors})
        except google.auth.exceptions.RefreshError:
            return jsonify({'success': False,
                'error': 'Google desconectado. Reconecta en /auth/google.'})
        except Exception as e:
            if _is_invalid_grant(e):
                return jsonify({'success': False,
                    'error': 'Google desconectado. Reconecta en /auth/google.'})
            return jsonify({'success': False, 'error': str(e)})

    # ============================================================
    #  APPOINTMENT UPDATE — re-sync Google Calendar on calendar change
    # ============================================================
    @app.route('/calendar/api/appointment/<aid>', methods=['PATCH'])
    @login_required
    def api_update_appointment(aid):
        """Update appointment fields.
        If calendar_id changes and the appointment is confirmed, deletes the
        old Google Calendar event and creates a new one in the correct calendar.
        """
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        d = request.get_json() or {}
        if not d: return jsonify({'success': False, 'error': 'Sin datos'})

        apts = app.supabase.get('appointments', {'id': aid},
            select='id,calendar_id,google_event_id,google_cal_id,status,'
                   'title,encargado,tema,client_name,client_email,'
                   'start_time,end_time,invitados,lugar,direccion,ciudad,mapa,notes,meeting_link')
        if not apts: return jsonify({'success': False, 'error': 'No encontrado'})
        apt = apts[0]

        new_cal_id = d.get('calendar_id')
        old_cal_id = apt.get('calendar_id')
        cal_changed = new_cal_id and new_cal_id != old_cal_id
        is_confirmed = apt.get('status') == 'confirmed'

        if cal_changed and apt.get('google_event_id') and is_confirmed:
            creds = get_google_creds(app)
            if creds:
                all_cals = _get_calendar_config(app)
                email_map, gcal_id_map = _make_cal_maps(all_cals)
                try:
                    service = build('calendar', 'v3', credentials=creds)
                    # Borrar del calendario anterior
                    old_gcal = apt.get('google_cal_id') or gcal_id_map.get(old_cal_id, 'primary')
                    try:
                        service.events().delete(
                            calendarId=old_gcal, eventId=apt['google_event_id']).execute()
                    except Exception:
                        pass
                    # Crear en el nuevo calendario
                    merged = {**apt, **d}
                    new_gcal = gcal_id_map.get(new_cal_id, 'primary')
                    attendees = _build_attendees(merged, email_map)
                    event = _build_google_event(merged, attendees)
                    created = service.events().insert(
                        calendarId=new_gcal, body=event, sendUpdates='all').execute()
                    d['google_event_id'] = created.get('id')
                    d['google_cal_id']   = new_gcal
                except Exception as e:
                    print(f'[api_update_appointment] Google error: {e}')

        ok = app.supabase.update('appointments', aid, d)
        return jsonify({'success': ok})

    # ============================================================
    #  PLANNING MODULE
    # ============================================================
    @app.route('/planning')
    @login_required
    def planning():
        if not user_can('planning'):
            flash('No tienes acceso al módulo Planificación.', 'warning')
            return redirect('/dashboard')
        ms_connected = bool(get_ms_token(app))
        return render_template('planning.html', ms_connected=ms_connected,
                               is_admin_user=is_admin(), scope='planning',
                               page_title='Planificación', page_sub='Proyectos y tareas internas del equipo')

    @app.route('/todo')
    @login_required
    def todo():
        if not user_can('todo'):
            flash('No tienes acceso al módulo To-Do externo.', 'warning')
            return redirect('/dashboard')
        ms_connected = bool(get_ms_token(app))
        return render_template('planning.html', ms_connected=ms_connected,
                               is_admin_user=is_admin(), scope='todo',
                               page_title='To-Do externo', page_sub='Tareas sincronizadas desde Microsoft To-Do')

    @app.route('/planning/api/projects', methods=['GET'])
    @login_required
    def planning_projects():
        rows = app.supabase.get('projects', select='*')
        return jsonify(rows or [])

    @app.route('/planning/api/projects', methods=['POST'])
    @login_required
    def planning_create_project():
        d = request.get_json() or {}
        d['created_by'] = current_user.id
        d['name'] = _sanitize(d.get('name', ''), 200)
        if not d['name']: return jsonify({'success': False, 'error': 'Nombre requerido'})
        r = app.supabase.insert('projects', d)
        return jsonify({'success': bool(r), 'project': r[0] if r else None})

    @app.route('/planning/api/projects/<pid>', methods=['PATCH'])
    @login_required
    def planning_update_project(pid):
        d = request.get_json() or {}
        ok = app.supabase.update('projects', pid, d)
        return jsonify({'success': ok})

    @app.route('/planning/api/projects/<pid>', methods=['DELETE'])
    @login_required
    def planning_delete_project(pid):
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        ok = app.supabase.delete('projects', pid)
        return jsonify({'success': ok})

    @app.route('/planning/api/tasks', methods=['GET'])
    @login_required
    def planning_tasks():
        pid    = request.args.get('project_id')
        scope  = request.args.get('scope', 'all')   # all | planning | todo
        if pid:
            rows = app.supabase.get('tasks', {'project_id': pid}, select='*')
        else:
            rows = app.supabase.get('tasks', select='*')
        rows = rows or []
        # Filtrar por scope (planning = manual; todo = MS)
        if scope == 'planning':
            rows = [t for t in rows if t.get('source') != 'ms_todo']
        elif scope == 'todo':
            rows = [t for t in rows if t.get('source') == 'ms_todo']
        # Permisos por usuario
        if not is_admin():
            allowed_ms  = set(get_user_ms_emails(app, current_user.id))
            has_todo    = 'todo'     in getattr(current_user, 'modules', [])
            has_plan    = 'planning' in getattr(current_user, 'modules', [])
            uid = str(current_user.id)
            def visible(t):
                if t.get('source') == 'ms_todo':
                    if not has_todo: return False
                    return (t.get('ms_email') or '') in allowed_ms
                # Tareas manuales
                if not has_plan: return False
                if t.get('created_by') == uid: return True
                if t.get('assigned_to') == uid: return True
                if (t.get('assigned_email') or '').lower() == (current_user.email or '').lower():
                    return True
                return False
            rows = [t for t in rows if visible(t)]
        return jsonify(rows)

    @app.route('/planning/api/ms-accounts', methods=['GET'])
    @login_required
    def planning_ms_accounts():
        if not is_admin(): return jsonify([])
        rows = app.supabase.get('ms_tokens', select='email') or []
        return jsonify([r.get('email') for r in rows if r.get('email')])

    @app.route('/planning/api/ms-lists', methods=['GET'])
    @login_required
    def planning_ms_lists():
        if not is_admin(): return jsonify([])
        email = request.args.get('email', '')
        if not email: return jsonify([])
        token = get_ms_token_for(app, email)
        if not token: return jsonify([])
        try:
            r = req_lib.get(f'{MS_GRAPH_URL}/me/todo/lists',
                            headers={'Authorization': f'Bearer {token}'}, timeout=(5,15))
            if r.status_code != 200: return jsonify([])
            lists = r.json().get('value', [])
            return jsonify([{'id': l['id'], 'name': l.get('displayName','To-Do')} for l in lists])
        except Exception:
            return jsonify([])

    @app.route('/planning/api/deps/<dep_id>', methods=['DELETE'])
    @login_required
    def planning_delete_dep(dep_id):
        ok = app.supabase.delete('task_deps', dep_id)
        return jsonify({'success': ok})

    @app.route('/planning/api/tasks/<tid>/refresh-subtasks', methods=['POST'])
    @login_required
    def planning_refresh_subtasks(tid):
        """Trae las subtareas más recientes desde Microsoft To-Do para una tarea concreta."""
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        rows = app.supabase.get('tasks', {'id': tid}, select='*')
        if not rows: return jsonify({'success': False, 'error': 'Tarea no encontrada'})
        task = rows[0]
        ms_email = task.get('ms_email'); list_id = task.get('ms_list_id')
        src_id   = task.get('source_id')
        if not (ms_email and list_id and src_id):
            return jsonify({'success': False, 'error': 'Esta tarea no es de Microsoft To-Do'})
        token = get_ms_token_for(app, ms_email)
        if not token: return jsonify({'success': False, 'error': 'Token MS no disponible'})
        headers = {'Authorization': f'Bearer {token}'}
        items = []
        url = f'{MS_GRAPH_URL}/me/todo/lists/{list_id}/tasks/{src_id}/checklistItems?$top=200'
        try:
            while url:
                r = req_lib.get(url, headers=headers, timeout=(5,15))
                if r.status_code != 200:
                    return jsonify({'success': False, 'error': f'MS error {r.status_code}'})
                d = r.json()
                items.extend(d.get('value', []))
                url = d.get('@odata.nextLink')
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:200]})
        subs = [{
            'id':   ci.get('id',''),
            'name': (ci.get('displayName') or '').strip(),
            'done': bool(ci.get('isChecked')),
            'checked_at': ci.get('checkedDateTime'),
        } for ci in items]
        prog = int(sum(1 for s in subs if s.get('done')) * 100 / len(subs)) if subs else (task.get('progress_pct') or 0)
        app.supabase.update('tasks', tid, {
            'subtasks':       subs,
            'progress_pct':   prog,
            'last_synced_at': datetime.now(timezone.utc).isoformat()})
        return jsonify({'success': True, 'count': len(subs), 'progress': prog, 'subtasks': subs})

    @app.route('/planning/api/tasks/<tid>/subtask/<sid>', methods=['PATCH'])
    @login_required
    def planning_toggle_subtask(tid, sid):
        """Marca/desmarca una subtarea. body: {done: true|false}"""
        body = request.get_json() or {}
        done = bool(body.get('done'))
        rows = app.supabase.get('tasks', {'id': tid}, select='*')
        if not rows: return jsonify({'success': False, 'error': 'Tarea no encontrada'})
        task = rows[0]
        subs = task.get('subtasks') or []
        changed = False
        for s in subs:
            if s.get('id') == sid:
                s['done'] = done
                s['checked_at'] = datetime.now(timezone.utc).isoformat() if done else None
                changed = True
                break
        if not changed: return jsonify({'success': False, 'error': 'Subtarea no encontrada'})
        # Recalcular progreso
        prog = int(sum(1 for s in subs if s.get('done')) * 100 / len(subs)) if subs else 0
        upd = {'subtasks': subs, 'progress_pct': prog,
               'updated_at': datetime.now(timezone.utc).isoformat()}
        ok = app.supabase.update('tasks', tid, upd)
        # Push a Microsoft si corresponde
        pushed = False
        if ok and task.get('source') == 'ms_todo' and task.get('ms_email') and task.get('ms_list_id') and task.get('source_id'):
            token = get_ms_token_for(app, task['ms_email'])
            if token:
                try:
                    r = req_lib.patch(
                        f"{MS_GRAPH_URL}/me/todo/lists/{task['ms_list_id']}/tasks/{task['source_id']}/checklistItems/{sid}",
                        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                        json={'isChecked': done}, timeout=(5,15))
                    pushed = r.status_code in (200, 204)
                except Exception:
                    pass
        return jsonify({'success': ok, 'pushed_to_ms': pushed, 'progress': prog})

    @app.route('/planning/api/tasks', methods=['POST'])
    @login_required
    def planning_create_task():
        d = request.get_json() or {}
        d['created_by'] = current_user.id
        d['title'] = _sanitize(d.get('title', ''), 300)
        if not d['title']: return jsonify({'success': False, 'error': 'Título requerido'})
        d.setdefault('status', 'pending')
        d.setdefault('priority', 'medium')
        d.setdefault('phase', 'General')
        d.setdefault('progress_pct', 0)
        d.setdefault('alert_days', 3)
        # Si se solicita sincronizar con MS, marcar como ms_todo
        if d.get('ms_email') and d.get('ms_list_id'):
            d.setdefault('source', 'ms_todo')
        r = app.supabase.insert('tasks', d)
        task = r[0] if r else None
        # Push a Microsoft si corresponde
        if task and task.get('ms_email') and task.get('ms_list_id'):
            ok, new_src = push_task_to_ms(app, task)
            if ok and new_src:
                app.supabase.update('tasks', task['id'],
                    {'source_id': new_src,
                     'last_synced_at': datetime.now(timezone.utc).isoformat()})
                task['source_id'] = new_src
        return jsonify({'success': bool(r), 'task': task})

    @app.route('/planning/api/tasks/<tid>', methods=['PATCH'])
    @login_required
    def planning_update_task(tid):
        d = request.get_json() or {}
        d['updated_at'] = datetime.now(timezone.utc).isoformat()
        if d.get('status') == 'done' and not d.get('completed_date'):
            d['completed_date'] = date.today().isoformat()
        ok = app.supabase.update('tasks', tid, d)
        pushed = False
        if ok:
            current = app.supabase.get('tasks', {'id': tid}, select='*')
            if current:
                pushed, new_src = push_task_to_ms(app, current[0])
                if pushed:
                    upd = {'last_synced_at': datetime.now(timezone.utc).isoformat()}
                    if new_src and not current[0].get('source_id'):
                        upd['source_id'] = new_src
                        upd['source']    = 'ms_todo'
                    app.supabase.update('tasks', tid, upd)
        return jsonify({'success': ok, 'pushed_to_ms': pushed})

    @app.route('/planning/api/tasks/<tid>', methods=['DELETE'])
    @login_required
    def planning_delete_task(tid):
        # Obtener tarea antes de borrar para poder eliminarla en MS
        rows = app.supabase.get('tasks', {'id': tid}, select='*')
        task = rows[0] if rows else None
        ok = app.supabase.delete('tasks', tid)
        deleted_ms = False
        if ok and task:
            deleted_ms = delete_task_in_ms(app, task)
        return jsonify({'success': ok, 'deleted_in_ms': deleted_ms})

    @app.route('/planning/api/deps/<tid>', methods=['GET'])
    @login_required
    def planning_task_deps(tid):
        rows = app.supabase.get('task_deps', {'task_id': tid}, select='depends_on')
        return jsonify([r['depends_on'] for r in (rows or [])])

    @app.route('/planning/api/deps', methods=['POST'])
    @login_required
    def planning_add_dep():
        d = request.get_json() or {}
        r = app.supabase.insert_ignore('task_deps', d)
        return jsonify({'success': bool(r)})

    @app.route('/planning/api/import-todo', methods=['POST'])
    @login_required
    def planning_import_todo():
        if not is_admin(): return jsonify({'success': False, 'error': 'Solo admin'})
        accounts = get_all_ms_tokens(app)
        # Filtro opcional: ?email=jomap@... para sincronizar una sola cuenta
        only_email = (request.args.get('email') or '').strip().lower()
        if only_email:
            accounts = [(e, t) for (e, t) in accounts if e.lower() == only_email]
        if not accounts:
            return jsonify({'success': False, 'needs_auth': True,
                'error': 'Microsoft To-Do no está conectado. Conecta primero desde Planificación.'})
        # Tope global de tiempo para no exceder timeout de gunicorn
        import time as _time
        DEADLINE = _time.monotonic() + 90
        status_map = {
            'notStarted': 'pending', 'inProgress': 'in_progress',
            'completed': 'done', 'waitingOnOthers': 'review', 'deferred': 'blocked'
        }
        prio_map = {'low': 'low', 'normal': 'medium', 'high': 'high'}

        # Pre-fetch ALL existing ms_todo source_ids once (massive speedup vs N+1)
        existing_rows = app.supabase.get('tasks', {'source': 'ms_todo'}, select='source_id') or []
        existing_ids = {r['source_id'] for r in existing_rows if r.get('source_id')}

        total_imported = 0; total_skipped = 0; total_errors = 0
        per_account = []
        sync_iso = datetime.now(timezone.utc).isoformat()
        try:
            partial = False
            for ms_email, token in accounts:
                if _time.monotonic() > DEADLINE:
                    partial = True
                    per_account.append(f'{ms_email}: pendiente (tiempo agotado, reintenta)')
                    continue
                headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
                imported = 0; skipped = 0; errors = 0
                # Get all To-Do task lists for this account
                r = req_lib.get(f'{MS_GRAPH_URL}/me/todo/lists', headers=headers, timeout=(8,20))
                if r.status_code == 401:
                    per_account.append(f'{ms_email}: token expirado, reconecta')
                    continue
                if r.status_code != 200:
                    per_account.append(f'{ms_email}: error {r.status_code}')
                    continue
                lists = r.json().get('value', [])
                for lst in lists:
                    if _time.monotonic() > DEADLINE: partial = True; break
                    list_id    = lst['id']
                    list_title = lst.get('displayName', 'To-Do')
                    # Import liviano: NO traemos subtareas en el sync masivo.
                    # Las subtareas se cargan bajo demanda al abrir cada tarea (auto-refresh-subtasks).
                    url = f'{MS_GRAPH_URL}/me/todo/lists/{list_id}/tasks?$top=100'
                    while url:
                        if _time.monotonic() > DEADLINE: partial = True; break
                        tr = req_lib.get(url, headers=headers, timeout=(10,20))
                        if tr.status_code != 200: break
                        tdata = tr.json()
                        batch = []
                        for task in tdata.get('value', []):
                            title = (task.get('title') or '').strip()
                            if not title: continue
                            tid = task.get('id', '')
                            if tid in existing_ids:
                                skipped += 1
                                continue
                            subs_for_new = []
                            due = None
                            if task.get('dueDateTime'):
                                try: due = task['dueDateTime']['dateTime'][:10]
                                except Exception: pass
                            comp = None
                            if task.get('completedDateTime'):
                                try: comp = task['completedDateTime']['dateTime'][:10]
                                except Exception: pass
                            subs = subs_for_new
                            # Si hay subtareas y el progreso no está en 100, calculamos progreso por subtareas
                            sub_progress = None
                            if subs:
                                done_n = sum(1 for s in subs if s.get('done'))
                                if subs and done_n < len(subs):
                                    sub_progress = int(done_n * 100 / len(subs))
                            td = {
                                'title':          title[:300],
                                'description':    (task.get('body') or {}).get('content', '')[:5000],
                                'status':         status_map.get(task.get('status','notStarted'), 'pending'),
                                'priority':       prio_map.get(task.get('importance','normal'), 'medium'),
                                'due_date':       due,
                                'completed_date': comp,
                                'tags':           f'{list_title} · {ms_email}',
                                'phase':          (list_title or 'General')[:100],
                                'source':         'ms_todo',
                                'source_id':      tid,
                                'ms_email':       ms_email,
                                'ms_list_id':     list_id,
                                'last_synced_at': sync_iso,
                                'created_by':     current_user.id,
                                'progress_pct':   100 if (task.get('status') == 'completed' and comp) else (sub_progress or 0),
                                'subtasks':       subs,
                            }
                            batch.append(td)
                            existing_ids.add(tid)
                        # BULK INSERT — un solo POST por página
                        if batch:
                            res = app.supabase.insert('tasks', batch)
                            if res is None:
                                errors += len(batch)
                            else:
                                imported += len(batch)
                        url = tdata.get('@odata.nextLink')
                per_account.append(f'{ms_email}: +{imported} nuevas, {skipped} ya existían')
                total_imported += imported
                total_skipped  += skipped
                total_errors   += errors
            return jsonify({'success': True, 'imported': total_imported,
                            'skipped': total_skipped, 'errors': total_errors,
                            'detail': per_account, 'partial': partial})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:300]})

    @app.route('/planning/api/export-excel')
    @login_required
    def planning_export_excel():
        if not OPENPYXL_AVAILABLE:
            return jsonify({'error': 'openpyxl no instalado'}), 500
        pid = request.args.get('project_id')
        tasks = (app.supabase.get('tasks', {'project_id': pid}, select='*')
                 if pid else app.supabase.get('tasks', select='*'))
        projects_map = {p['id']: p['name']
                        for p in (app.supabase.get('projects', select='id,name') or [])}
        wb = openpyxl.Workbook()
        ws = wb.active; ws.title = 'Tareas'
        hdr_fill = PatternFill('solid', fgColor='4F46E5')
        hdr_font = Font(color='FFFFFF', bold=True)
        thin = Side(style='thin', color='CCCCCC')
        brd  = Border(left=thin, right=thin, top=thin, bottom=thin)
        headers = ['Proyecto','Fase','Título','Descripción','Estado','Prioridad',
                   'Asignado a','Email','F. Inicio','F. Vencimiento',
                   'Días restantes','Progreso %','Alerta (días)','Etiquetas','Notas','Fuente']
        for i, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=i, value=h)
            c.fill = hdr_fill; c.font = hdr_font
            c.alignment = Alignment(horizontal='center', vertical='center')
            c.border = brd
        ws.row_dimensions[1].height = 20
        today_d = date.today()
        for ri, t in enumerate(tasks or [], 2):
            due_str = (t.get('due_date') or '')[:10]
            days_left = None
            if due_str:
                try:
                    days_left = (datetime.strptime(due_str,'%Y-%m-%d').date() - today_d).days
                except Exception: pass
            row = [
                projects_map.get(t.get('project_id'), ''),
                t.get('phase',''), t.get('title',''), t.get('description',''),
                t.get('status',''), t.get('priority',''),
                t.get('assigned_to',''), t.get('assigned_email',''),
                t.get('start_date','')[:10] if t.get('start_date') else '',
                due_str, days_left,
                t.get('progress_pct',0), t.get('alert_days',3),
                t.get('tags',''), t.get('notes',''), t.get('source',''),
            ]
            for ci, val in enumerate(row, 1):
                c = ws.cell(row=ri, column=ci, value=val)
                c.border = brd
                if ci == 11 and val is not None:
                    c.font = Font(color='EF4444' if val < 0 else ('F59E0B' if val <= 3 else '000000'))
        for col in ws.columns:
            ml = max((len(str(c.value or '')) for c in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(ml + 3, 50)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        fname = f'tareas_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx'
        return send_file(buf, as_attachment=True, download_name=fname,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.route('/planning/api/import-excel', methods=['POST'])
    @login_required
    def planning_import_excel():
        if not OPENPYXL_AVAILABLE:
            return jsonify({'success': False, 'error': 'openpyxl no instalado'}), 500
        f = request.files.get('file')
        if not f: return jsonify({'success': False, 'error': 'No se recibió archivo'})
        try:
            wb = openpyxl.load_workbook(io.BytesIO(f.read()), data_only=True)
            ws = wb.active
            raw_headers = [str(c.value or '').strip().lower() for c in ws[1]]
            # Normalize headers — support Spanish and English
            alias = {
                'título': 'title', 'titulo': 'title',
                'descripción': 'description', 'descripcion': 'description',
                'fase': 'phase',
                'estado': 'status',
                'prioridad': 'priority',
                'asignado a': 'assigned_to',
                'f. inicio': 'start_date', 'fecha inicio': 'start_date',
                'f. vencimiento': 'due_date', 'fecha vencimiento': 'due_date',
                'progreso %': 'progress_pct', 'progreso': 'progress_pct',
                'alerta (días)': 'alert_days', 'alerta días': 'alert_days',
                'etiquetas': 'tags',
                'notas': 'notes',
                'email': 'assigned_email',
                'proyecto': '_project_name',
            }
            headers = [alias.get(h, h) for h in raw_headers]
            # Build project name map
            proj_by_name = {p['name'].lower(): p['id']
                            for p in (app.supabase.get('projects', select='id,name') or [])}
            imported = 0; errors = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                if all(v is None or str(v).strip() == '' for v in row): continue
                rd = dict(zip(headers, row))
                title = str(rd.get('title', '')).strip()
                if not title: continue
                try:
                    td = {
                        'title': title,
                        'description': str(rd.get('description', '') or ''),
                        'phase': str(rd.get('phase', 'General') or 'General').strip(),
                        'status': str(rd.get('status', 'pending') or 'pending').lower().replace(' ','_'),
                        'priority': str(rd.get('priority', 'medium') or 'medium').lower(),
                        'assigned_to': str(rd.get('assigned_to', '') or ''),
                        'assigned_email': str(rd.get('assigned_email', '') or ''),
                        'tags': str(rd.get('tags', '') or ''),
                        'notes': str(rd.get('notes', '') or ''),
                        'source': 'excel',
                        'created_by': current_user.id,
                    }
                    # Map project name → id
                    pname = str(rd.get('_project_name', '') or '').strip().lower()
                    if pname and pname in proj_by_name:
                        td['project_id'] = proj_by_name[pname]
                    # Parse dates
                    for fld in ('start_date', 'due_date'):
                        val = rd.get(fld)
                        if val:
                            if isinstance(val, datetime): td[fld] = val.strftime('%Y-%m-%d')
                            elif isinstance(val, date):   td[fld] = val.isoformat()
                            elif str(val).strip():
                                try: td[fld] = datetime.strptime(str(val).strip()[:10],'%Y-%m-%d').strftime('%Y-%m-%d')
                                except Exception: pass
                    # Progress
                    try: td['progress_pct'] = max(0, min(100, int(float(str(rd.get('progress_pct') or 0)))))
                    except Exception: td['progress_pct'] = 0
                    try: td['alert_days'] = max(0, int(float(str(rd.get('alert_days') or 3))))
                    except Exception: td['alert_days'] = 3
                    app.supabase.insert('tasks', td)
                    imported += 1
                except Exception: errors += 1
            return jsonify({'success': True, 'imported': imported, 'errors': errors})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    # ============================================================
    #  UTILITY
    # ============================================================
    @app.route('/health')
    def health():
        return jsonify({'status': 'ok'})

    @app.route('/api/google-status')
    @login_required
    def google_status():
        if not is_admin():
            return jsonify({'connected': False, 'error': 'No autorizado'})
        tokens = app.supabase.get('google_tokens', {'email': GOOGLE_ACCOUNT_EMAIL})
        if not tokens:
            return jsonify({'connected': False, 'message': 'No hay token. Ve a /auth/google.'})
        t = tokens[0]
        creds = get_google_creds(app)
        if creds:
            return jsonify({'connected': True, 'email': t['email'],
                'expiry': t.get('token_expiry'), 'has_refresh_token': bool(t.get('refresh_token'))})
        return jsonify({'connected': False, 'email': t['email'],
            'message': 'Token invalido. Reconecta en /auth/google.'})

    return app
