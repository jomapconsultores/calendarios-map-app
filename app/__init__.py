from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from config.config import Config
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import google.auth.exceptions
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from collections import defaultdict, OrderedDict
import os, requests as req_lib, traceback, pytz, json, re, time

GOOGLE_SCOPES = ['https://www.googleapis.com/auth/calendar']
GOOGLE_ACCOUNT_EMAIL = 'mposligua0000@gmail.com'

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
_user_cal_cache = TTLCache(ttl=90)    # user calendars  — 90 s
_google_cache   = TTLCache(ttl=120)   # google status   — 2 min


# ============================================================
#  USER MODEL
# ============================================================
class User(UserMixin):
    def __init__(self, d):
        self.id = d.get('id'); self.email = d.get('email')
        self.full_name = d.get('full_name'); self.role = d.get('role', 'staff')
        self.is_admin = d.get('role') == 'admin'


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
    result = app.supabase.get('calendar_config', select='calendar_id,name,email,color')
    _cal_cache.set('all', result)
    return result

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

    @login_manager.user_loader
    def load_user(uid):
        if app.supabase:
            u = app.supabase.get('users', {'id': uid}, select='id,email,full_name,role')
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
        return render_template('profile.html')

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
        users     = app.supabase.get('users', select='id,email,full_name,role,created_at')
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
        return render_template('admin_users.html', users=users, calendarios=all_cals,
                               pending=pending, pending_all=pending_all)

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
        flash('Registro actualizado', 'success')
        return redirect('/admin/database')

    @app.route('/admin/database/delete', methods=['POST'])
    @login_required
    def admin_db_delete():
        if not is_admin(): return jsonify({'success': False})
        app.supabase.delete(request.form.get('table'), request.form.get('id'))
        flash('Registro eliminado', 'success')
        return redirect('/admin/database')

    @app.route('/admin/database/insert', methods=['POST'])
    @login_required
    def admin_db_insert():
        if not is_admin(): return jsonify({'success': False})
        table = request.form.get('table')
        data = {k: v for k, v in request.form.items() if k not in ['table']}
        if data: app.supabase.insert(table, data)
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
        cal_ids = request.form.getlist('calendars')
        if cal_ids:
            for p in app.supabase.get('calendar_permissions', {'user_id': uid}, select='id'):
                app.supabase.delete('calendar_permissions', p['id'])
            for cal_id in cal_ids:
                app.supabase.insert('calendar_permissions',
                    {'user_id': uid, 'calendar_id': cal_id, 'status': 'approved'})
        _user_cal_cache.invalidate(uid)  # bust cache
        flash('Usuario actualizado', 'success')
        return redirect('/admin/users')

    @app.route('/admin/user/delete/<uid>', methods=['POST'])
    @login_required
    def admin_delete_user(uid):
        if not is_admin(): return jsonify({'success': False})
        for p in app.supabase.get('calendar_permissions', {'user_id': uid}, select='id'):
            app.supabase.delete('calendar_permissions', p['id'])
        app.supabase.delete('users', uid)
        _user_cal_cache.invalidate(uid)
        flash('Usuario eliminado', 'success')
        return redirect('/admin/users')

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
        return render_template('dashboard.html', calendarios=cals, pending=pending,
                               pending_all=pending_all, google_connected=google_ok)

    # ============================================================
    #  CALENDAR VIEW
    # ============================================================
    @app.route('/calendar')
    @login_required
    def calendar():
        cals = (_get_calendar_config(app) if is_admin()
                else get_user_calendars(app, current_user.id))
        return render_template('calendar.html', calendarios=cals,
                               google_connected=get_google_creds(app) is not None)

    # ============================================================
    #  API — EVENTS  (single query with IN filter for non-admin)
    # ============================================================
    APPT_SELECT = ('id,title,encargado,start_time,end_time,status,calendar_id,'
                   'tema,client_name,client_email,notes,lugar,direccion,mapa,'
                   'ciudad,meeting_link,google_event_id,is_recurring,parent_event_id')

    @app.route('/calendar/api/events')
    @login_required
    def api_events():
        if is_admin():
            events = app.supabase.get('appointments', select=APPT_SELECT)
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            if not ucal:
                return jsonify([])
            # One query with IN — replaces N separate queries
            events = app.supabase.get_in('appointments', 'calendar_id', ucal,
                                         select=APPT_SELECT)
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
                    select='id,title,encargado,tema,client_name,start_time,is_recurring')
            ]
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            if not ucal:
                return jsonify([])
            all_p = app.supabase.get_in('appointments', 'calendar_id', ucal,
                select='id,title,encargado,tema,client_name,start_time,calendar_id,status,is_recurring')
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

            if not all([title, cal_id, encargado, tema, date_str, time_str]):
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

            # ---- Recurring ----
            is_recurring = request.form.get('is_recurring') == 'true'
            if is_recurring:
                rec_days_str = request.form.get('recurrence_days', '[]')
                rec_end_str  = request.form.get('recurrence_end_date', '')
                try:
                    rec_days = json.loads(rec_days_str)
                    rec_end  = datetime.strptime(rec_end_str, '%Y-%m-%d').date()
                    start_d  = datetime.strptime(date_str, '%Y-%m-%d').date()
                except Exception as ex:
                    return jsonify({'success': False, 'error': f'Datos de recurrencia invalidos: {ex}'})
                if not rec_days:
                    return jsonify({'success': False, 'error': 'Selecciona al menos un dia'})
                if rec_end < start_d:
                    return jsonify({'success': False, 'error': 'Fecha fin debe ser posterior a inicio'})

                dates_to_create = []
                cur_d = start_d
                while cur_d <= rec_end:
                    if cur_d.weekday() in rec_days:
                        dates_to_create.append(cur_d)
                    cur_d += timedelta(days=1)
                if not dates_to_create:
                    return jsonify({'success': False, 'error': 'Ningun dia coincide con la seleccion'})
                if len(dates_to_create) > 60:
                    return jsonify({'success': False, 'error': 'Maximo 60 eventos por serie'})

                rec_notes  = f'[SERIE {len(dates_to_create)} eventos] {notes}'.strip()
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
                    r = app.supabase.insert('appointments', record)
                    if not r:
                        del record['is_recurring']
                        record.pop('parent_event_id', None)
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
                    return jsonify({'success': True, 'count': len(created_ids), 'recurring': True})
                return jsonify({'success': False, 'error': 'No se pudieron crear los eventos'})

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
                   'status,calendar_id,invitados,lugar,direccion,ciudad,mapa,notes,meeting_link')
        if not apts: return jsonify({'success': False})
        apt = apts[0]
        if not is_admin() and not user_has_calendar_access(app, current_user.id, apt.get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        creds = get_google_creds(app)
        if not creds:
            app.supabase.update('appointments', aid, {'status': 'confirmed'})
            return jsonify({'success': True, 'message': 'Aprobada (sin sincronizacion Google)'})
        try:
            service   = build('calendar', 'v3', credentials=creds)
            all_cals  = _get_calendar_config(app)
            cal_map   = {c['calendar_id']: c['email'] for c in all_cals if c.get('email')}
            attendees = []
            cal_email = cal_map.get(apt.get('calendar_id'))
            if cal_email: attendees.append({'email': cal_email})
            if apt.get('invitados'):
                for inv in apt['invitados'].split(','):
                    inv = inv.strip()
                    if inv and inv not in [a['email'] for a in attendees]:
                        attendees.append({'email': inv})
            if not attendees: attendees.append({'email': GOOGLE_ACCOUNT_EMAIL})
            desc = (f"Titulo: {apt['title']}\nEncargado: {apt.get('encargado','')}"
                    f"\nTema: {apt.get('tema','')}")
            if apt.get('client_name'): desc += f"\nCliente: {apt['client_name']}"
            if apt.get('lugar'): desc += f"\nLugar: {apt['lugar']}"
            if apt.get('direccion'): desc += f"\nDireccion: {apt['direccion']}"
            if apt.get('ciudad'): desc += f"\nCiudad: {apt['ciudad']}"
            if apt.get('mapa'): desc += f"\nMapa: {apt['mapa']}"
            if apt.get('notes'): desc += f"\nNotas: {apt['notes']}"
            location = ''
            if apt.get('direccion'):
                location = apt['direccion']
                if apt.get('ciudad'): location += f", {apt['ciudad']}, Ecuador"
                if apt.get('lugar'): location = f"{apt['lugar']}, {location}"
            event = {
                'summary': f"{apt['title']} - {apt.get('encargado','')}",
                'description': desc,
                'start': {'dateTime': apt['start_time'], 'timeZone': 'America/Guayaquil'},
                'end':   {'dateTime': apt['end_time'],   'timeZone': 'America/Guayaquil'},
                'attendees': attendees,
                'reminders': {'useDefault': False, 'overrides': [
                    {'method': 'email', 'minutes': 1440},
                    {'method': 'popup', 'minutes': 30}]},
            }
            if location: event['location'] = location
            existing = service.events().list(
                calendarId='primary', timeMin=apt['start_time'],
                timeMax=apt['end_time'], q=apt['title'], maxResults=1).execute()
            created = (existing['items'][0] if existing.get('items')
                       else service.events().insert(
                            calendarId='primary', body=event, sendUpdates='all').execute())
            app.supabase.update('appointments', aid,
                {'status': 'confirmed', 'google_event_id': created.get('id')})
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
            select='id,calendar_id,google_event_id')
        if not apts: return jsonify({'success': False})
        apt = apts[0]
        if not is_admin() and not user_has_calendar_access(app, current_user.id, apt.get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        if apt.get('google_event_id'):
            creds = get_google_creds(app)
            if creds:
                try:
                    build('calendar', 'v3', credentials=creds).events().delete(
                        calendarId='primary', eventId=apt['google_event_id']).execute()
                except Exception:
                    pass
        app.supabase.delete('appointments', aid)
        return jsonify({'success': True})

    @app.route('/calendar/api/delete-series/<parent_id>', methods=['POST'])
    @login_required
    def api_delete_series(parent_id):
        all_apts = app.supabase.get('appointments',
            select='id,calendar_id,google_event_id,parent_event_id')
        series = [a for a in all_apts
                  if a.get('parent_event_id') == parent_id or a.get('id') == parent_id]
        if not series: return jsonify({'success': False, 'error': 'Serie no encontrada'})
        if not is_admin() and not user_has_calendar_access(
                app, current_user.id, series[0].get('calendar_id')):
            return jsonify({'success': False, 'error': 'Sin autorizacion'})
        creds = get_google_creds(app); deleted = 0
        for apt in series:
            if apt.get('google_event_id') and creds:
                try:
                    build('calendar', 'v3', credentials=creds).events().delete(
                        calendarId='primary', eventId=apt['google_event_id']).execute()
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
        cal_map  = {c['calendar_id']: c['email'] for c in all_cals if c.get('email')}
        for apt in app.supabase.get('appointments',
                select='id,title,encargado,tema,start_time,end_time,calendar_id,'
                       'invitados,direccion,ciudad,lugar,status,google_event_id'):
            if apt.get('status') != 'confirmed' or apt.get('google_event_id'):
                continue
            try:
                service  = build('calendar', 'v3', credentials=creds)
                existing = service.events().list(
                    calendarId='primary', timeMin=apt['start_time'],
                    timeMax=apt['end_time'], q=apt['title'], maxResults=1).execute()
                if existing.get('items'):
                    app.supabase.update('appointments', apt['id'],
                        {'google_event_id': existing['items'][0]['id']})
                    skipped += 1; continue
                attendees = []
                ce = cal_map.get(apt.get('calendar_id'))
                if ce: attendees.append({'email': ce})
                if apt.get('invitados'):
                    for inv in apt['invitados'].split(','):
                        inv = inv.strip()
                        if inv and inv not in [a['email'] for a in attendees]:
                            attendees.append({'email': inv})
                if not attendees: attendees.append({'email': GOOGLE_ACCOUNT_EMAIL})
                location = apt.get('direccion', '')
                if location and apt.get('ciudad'): location += f", {apt['ciudad']}, Ecuador"
                if location and apt.get('lugar'): location = f"{apt['lugar']}, {location}"
                event = {
                    'summary': f"{apt['title']} - {apt.get('encargado','')}",
                    'description': f"Titulo: {apt['title']}\nEncargado: {apt.get('encargado','')}\nTema: {apt.get('tema','')}",
                    'start': {'dateTime': apt['start_time'], 'timeZone': 'America/Guayaquil'},
                    'end':   {'dateTime': apt['end_time'],   'timeZone': 'America/Guayaquil'},
                    'attendees': attendees,
                    'reminders': {'useDefault': False, 'overrides': [
                        {'method': 'email', 'minutes': 1440},
                        {'method': 'popup', 'minutes': 30}]},
                }
                if location: event['location'] = location
                created = service.events().insert(calendarId='primary',
                    body=event, sendUpdates='all').execute()
                app.supabase.update('appointments', apt['id'],
                    {'google_event_id': created.get('id')})
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
