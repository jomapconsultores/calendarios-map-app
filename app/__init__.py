from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from config.config import Config
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os, requests, traceback

load_dotenv()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

login_manager = LoginManager()

class User(UserMixin):
    def __init__(self, d):
        self.id = d.get('id')
        self.email = d.get('email')
        self.full_name = d.get('full_name')
        self.role = d.get('role', 'staff')
        self.is_admin = d.get('role') == 'admin'

class SupabaseAPI:
    def __init__(self, url, key):
        self.url = url
        self.headers = {'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    def get(self, table, filters=None):
        q = f'{self.url}/rest/v1/{table}?select=*'
        if filters:
            for k, v in filters.items(): q += f'&{k}=eq.{v}'
        r = requests.get(q, headers=self.headers)
        return r.json() if r.status_code == 200 else []
    def insert(self, table, data):
        h = self.headers.copy(); h['Prefer'] = 'return=representation'
        r = requests.post(f'{self.url}/rest/v1/{table}', headers=h, json=data)
        if r.status_code in [200, 201]: return r.json() if isinstance(r.json(), list) else [r.json()]
        print(f"INSERT ERROR {r.status_code}: {r.text}")
        return None
    def update(self, table, id_val, data, id_col='id'):
        r = requests.patch(f'{self.url}/rest/v1/{table}?{id_col}=eq.{id_val}', headers=self.headers, json=data)
        return r.status_code in [200, 204]
    def delete(self, table, id_val, id_col='id'):
        r = requests.delete(f'{self.url}/rest/v1/{table}?{id_col}=eq.{id_val}', headers=self.headers)
        return r.status_code in [200, 204]

def get_google_creds(app):
    try:
        tokens = app.supabase.get('google_tokens', {'email': 'mposligua0000@gmail.com'})
        if tokens:
            t = tokens[0]
            return Credentials(token=t.get('token'), refresh_token=t.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token', client_id=app.config['GOOGLE_CLIENT_ID'],
                client_secret=app.config['GOOGLE_CLIENT_SECRET'], scopes=['https://www.googleapis.com/auth/calendar'])
    except: pass
    return None

def save_google_creds(app, creds):
    app.supabase.delete('google_tokens', 'mposligua0000@gmail.com', 'email')
    app.supabase.insert('google_tokens', {'email': 'mposligua0000@gmail.com', 'token': creds.token, 'refresh_token': creds.refresh_token})

def get_user_calendars(app, uid):
    perms = app.supabase.get('calendar_permissions', {'user_id': uid, 'status': 'approved'})
    cal_ids = [p['calendar_id'] for p in perms]
    if not cal_ids: return []
    return [c for c in app.supabase.get('calendar_config') if c['calendar_id'] in cal_ids]

def is_admin():
    return current_user.is_authenticated and current_user.role == 'admin'

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.from_object(Config)
    try:
        app.supabase = SupabaseAPI(app.config['SUPABASE_URL'], app.config['SUPABASE_KEY'])
        print("✅ Supabase OK")
    except Exception as e:
        print(f"❌ Supabase: {e}"); app.supabase = None
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(uid):
        if app.supabase:
            u = app.supabase.get('users', {'id': uid})
            if u: return User(u[0])
        return None

    @app.route('/')
    def home():
        return redirect('/dashboard') if current_user.is_authenticated else render_template('index.html')

    @app.route('/dashboard')
    @login_required
    def dashboard():
        if is_admin():
            cals = app.supabase.get('calendar_config')
            pending_raw = app.supabase.get('calendar_permissions', {'status': 'pending'})
            seen = set()
            pending = []
            for p in pending_raw:
                if p['user_id'] not in seen:
                    seen.add(p['user_id'])
                    u = app.supabase.get('users', {'id': p['user_id']})
                    if u:
                        p['user_name'] = u[0].get('full_name', '')
                        p['user_email'] = u[0].get('email', '')
                    all_up = app.supabase.get('calendar_permissions', {'user_id': p['user_id'], 'status': 'pending'})
                    cal_ids = [ap['calendar_id'] for ap in all_up]
                    p['calendars'] = [c for c in app.supabase.get('calendar_config') if c['calendar_id'] in cal_ids]
                    pending.append(p)
        else:
            cals = get_user_calendars(app, current_user.id)
            pending = []
        google_ok = get_google_creds(app) is not None
        return render_template('dashboard.html', calendarios=cals, pending=pending, google_connected=google_ok)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email')
            pw = request.form.get('password')
            users = app.supabase.get('users', {'email': email})
            if users and check_password_hash(users[0]['password_hash'], pw):
                login_user(User(users[0]))
                return redirect('/dashboard')
            flash('Email o contraseña incorrectos', 'danger')
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            name = request.form.get('full_name')
            cals = request.form.getlist('calendars')
            existing = app.supabase.get('users', {'email': email})
            if existing:
                flash('Este email ya está registrado.', 'warning')
                return render_template('register.html', calendarios=app.supabase.get('calendar_config'))
            data = {'email': email, 'password_hash': generate_password_hash(password), 'full_name': name, 'role': 'staff'}
            result = app.supabase.insert('users', data)
            if result:
                uid = result[0]['id']
                for cal_id in cals:
                    app.supabase.insert('calendar_permissions', {'user_id': uid, 'calendar_id': cal_id, 'status': 'pending'})
                flash('✅ Registro enviado. Espera aprobación.', 'success')
                return redirect('/login')
            flash('Error al registrar', 'danger')
        return render_template('register.html', calendarios=app.supabase.get('calendar_config'))

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect('/')

    @app.route('/auth/google')
    @login_required
    def google_auth():
        flow = Flow.from_client_config({
            'web': {'client_id': app.config['GOOGLE_CLIENT_ID'], 'client_secret': app.config['GOOGLE_CLIENT_SECRET'],
                    'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
                    'redirect_uris': [app.config['GOOGLE_REDIRECT_URI']]}},
            scopes=['https://www.googleapis.com/auth/calendar'])
        flow.redirect_uri = app.config['GOOGLE_REDIRECT_URI']
        auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
        session['state'] = state
        return redirect(auth_url)

    @app.route('/auth/google/callback')
    @login_required
    def google_callback():
        flow = Flow.from_client_config({
            'web': {'client_id': app.config['GOOGLE_CLIENT_ID'], 'client_secret': app.config['GOOGLE_CLIENT_SECRET'],
                    'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
                    'redirect_uris': [app.config['GOOGLE_REDIRECT_URI']]}},
            scopes=['https://www.googleapis.com/auth/calendar'], state=session['state'])
        flow.redirect_uri = app.config['GOOGLE_REDIRECT_URI']
        flow.fetch_token(authorization_response=request.url)
        save_google_creds(app, flow.credentials)
        flash('✅ Google Calendar conectado!', 'success')
        return redirect('/dashboard')

    # ============= ADMIN PANEL =============

    @app.route('/admin/users')
    @login_required
    def admin_users():
        if not is_admin(): return redirect('/dashboard')
        users = app.supabase.get('users')
        all_cals = app.supabase.get('calendar_config')
        for u in users:
            perms = app.supabase.get('calendar_permissions', {'user_id': u['id'], 'status': 'approved'})
            cal_ids = [p['calendar_id'] for p in perms]
            u['calendars'] = [c for c in all_cals if c['calendar_id'] in cal_ids]
        return render_template('admin_users.html', users=users, calendarios=all_cals)

    @app.route('/admin/user/update/<uid>', methods=['POST'])
    @login_required
    def admin_update_user(uid):
        if not is_admin(): return redirect('/dashboard')
        data = {}
        if request.form.get('full_name'): data['full_name'] = request.form.get('full_name')
        if request.form.get('email'): data['email'] = request.form.get('email')
        if request.form.get('password'): data['password_hash'] = generate_password_hash(request.form.get('password'))
        if request.form.get('role'): data['role'] = request.form.get('role')
        if data: app.supabase.update('users', uid, data)
        cal_ids = request.form.getlist('calendars')
        if cal_ids:
            for p in app.supabase.get('calendar_permissions', {'user_id': uid}):
                app.supabase.delete('calendar_permissions', p['id'])
            for cal_id in cal_ids:
                app.supabase.insert('calendar_permissions', {'user_id': uid, 'calendar_id': cal_id, 'status': 'approved'})
        flash('Usuario actualizado', 'success')
        return redirect('/admin/users')

    @app.route('/admin/user/delete/<uid>', methods=['POST'])
    @login_required
    def admin_delete_user(uid):
        if not is_admin(): return {'success': False}
        for p in app.supabase.get('calendar_permissions', {'user_id': uid}):
            app.supabase.delete('calendar_permissions', p['id'])
        for a in app.supabase.get('appointments', {'created_by': uid}):
            app.supabase.delete('appointments', a['id'])
        app.supabase.delete('users', uid)
        flash('Usuario eliminado', 'success')
        return redirect('/admin/users')

    @app.route('/admin/approve-one/<pid>/<cal_id>', methods=['POST'])
    @login_required
    def admin_approve_one(pid, cal_id):
        if not is_admin(): return {'success': False}
        app.supabase.update('calendar_permissions', pid, {'status': 'approved'})
        return {'success': True}

    @app.route('/admin/approve-all/<uid>', methods=['POST'])
    @login_required
    def admin_approve_all(uid):
        if not is_admin(): return {'success': False}
        for p in app.supabase.get('calendar_permissions', {'user_id': uid, 'status': 'pending'}):
            app.supabase.update('calendar_permissions', p['id'], {'status': 'approved'})
        return {'success': True}

    @app.route('/admin/reject-all/<uid>', methods=['POST'])
    @login_required
    def admin_reject_all(uid):
        if not is_admin(): return {'success': False}
        for p in app.supabase.get('calendar_permissions', {'user_id': uid, 'status': 'pending'}):
            app.supabase.update('calendar_permissions', p['id'], {'status': 'rejected'})
        return {'success': True}

    # ============= CALENDARIO =============

    @app.route('/calendar')
    @login_required
    def calendar():
        cals = app.supabase.get('calendar_config') if is_admin() else get_user_calendars(app, current_user.id)
        return render_template('calendar.html', calendarios=cals, google_connected=get_google_creds(app) is not None)

    @app.route('/calendar/api/events')
    @login_required
    def api_events():
        if is_admin():
            events = app.supabase.get('appointments')
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            events = []
            for cid in ucal: events.extend(app.supabase.get('appointments', {'calendar_id': cid}))
        colors = {'pending': '#ffc107', 'confirmed': '#28a745', 'cancelled': '#dc3545'}
        return [{'id': e['id'], 'title': f"{e['title']} - {e.get('encargado', '')}",
                 'start': e['start_time'], 'end': e['end_time'],
                 'backgroundColor': colors.get(e.get('status'), '#007bff'),
                 'borderColor': colors.get(e.get('status'), '#007bff'),
                 'extendedProps': {'title': e.get('title', ''), 'encargado': e.get('encargado', ''),
                 'tema': e.get('tema', ''), 'client_name': e.get('client_name', ''),
                 'client_email': e.get('client_email', ''), 'status': e.get('status', 'pending'),
                 'calendar_id': e.get('calendar_id', ''), 'notes': e.get('notes', ''),
                 'google_event_id': e.get('google_event_id', '')}} for e in events]

    @app.route('/calendar/api/titles')
    @login_required
    def api_titles(): return [t['title'] for t in app.supabase.get('appointment_titles')]

    @app.route('/calendar/api/encargados')
    @login_required
    def api_encargados(): return [e['name'] for e in app.supabase.get('encargados')]

    @app.route('/calendar/api/temas')
    @login_required
    def api_temas(): return [t['description'] for t in app.supabase.get('temas')]

    @app.route('/calendar/api/clients')
    @login_required
    def api_clients(): return [{'name': c['name'], 'email': c.get('email', '')} for c in app.supabase.get('clients')]

    @app.route('/calendar/api/book', methods=['POST'])
    @login_required
    def api_book():
        try:
            date = request.form.get('date'); time = request.form.get('time')
            dur = int(request.form.get('duration', 30))
            start = f"{date}T{time}:00"
            end = (datetime.fromisoformat(start) + timedelta(minutes=dur)).isoformat()
            cal_id = request.form.get('calendar_id', 'personal')
            title = request.form.get('title', '').strip().upper()
            encargado = request.form.get('encargado', '').strip().upper()
            tema = request.form.get('tema', '').strip()
            client_name = request.form.get('client_name', '').strip().upper()
            client_email = request.form.get('client_email', '').strip()
            notificar = request.form.getlist('notificar')
            if not title or not cal_id or not encargado or not tema:
                return {'success': False, 'error': 'Faltan campos obligatorios'}
            if title and not app.supabase.get('appointment_titles', {'title': title}):
                app.supabase.insert('appointment_titles', {'title': title, 'calendar_id': cal_id})
            if encargado and not app.supabase.get('encargados', {'name': encargado}):
                app.supabase.insert('encargados', {'name': encargado})
            if tema and not app.supabase.get('temas', {'description': tema}):
                app.supabase.insert('temas', {'description': tema, 'calendar_id': cal_id})
            if client_name and not app.supabase.get('clients', {'name': client_name}):
                app.supabase.insert('clients', {'name': client_name, 'email': client_email, 'created_by': current_user.id})
            data = {'title': title, 'calendar_id': cal_id, 'encargado': encargado, 'tema': tema,
                    'client_name': client_name, 'client_email': client_email, 'start_time': start,
                    'end_time': end, 'status': 'pending', 'notes': request.form.get('notes', ''),
                    'invitados': ','.join(notificar) if notificar else '', 'created_by': current_user.id}
            result = app.supabase.insert('appointments', data)
            return {'success': True, 'id': result[0]['id']} if result else {'success': False, 'error': 'Error BD'}
        except Exception as e:
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    @app.route('/calendar/api/pending')
    @login_required
    def api_pending():
        events = app.supabase.get('appointments')
        if is_admin():
            pending = [a for a in events if a.get('status') == 'pending']
        else:
            ucal = [c['calendar_id'] for c in get_user_calendars(app, current_user.id)]
            pending = [a for a in events if a.get('status') == 'pending' and a.get('calendar_id') in ucal]
        return [{'id': a['id'], 'title': a['title'], 'encargado': a.get('encargado', ''),
                 'tema': a.get('tema', ''), 'client_name': a.get('client_name', ''),
                 'date': a['start_time'].split('T')[0], 'time': a['start_time'].split('T')[1][:5],
                 'calendar_id': a.get('calendar_id', '')} for a in pending]

    @app.route('/calendar/api/approve/<aid>', methods=['POST'])
    @login_required
    def api_approve(aid):
        apts = app.supabase.get('appointments', {'id': aid})
        if not apts: return {'success': False}
        apt = apts[0]
        creds = get_google_creds(app)
        if not creds:
            app.supabase.update('appointments', aid, {'status': 'confirmed'})
            return {'success': True, 'message': 'Aprobada'}
        try:
            service = build('calendar', 'v3', credentials=creds)
            cal_map = {c['calendar_id']: c['email'] for c in app.supabase.get('calendar_config') if c.get('email')}
            attendees = []
            cal_email = cal_map.get(apt.get('calendar_id'))
            if cal_email: attendees.append({'email': cal_email})
            if apt.get('invitados'):
                for inv in apt['invitados'].split(','):
                    inv = inv.strip()
                    if inv and inv not in [a['email'] for a in attendees]: attendees.append({'email': inv})
            if not attendees: attendees.append({'email': 'mposligua0000@gmail.com'})
            desc = f"Titulo: {apt['title']}\nEncargado: {apt.get('encargado','')}\nTema: {apt.get('tema','')}"
            if apt.get('client_name'): desc += f"\nCliente: {apt['client_name']}"
            if apt.get('notes'): desc += f"\nNotas: {apt['notes']}"
            event = {'summary': f"{apt['title']} - {apt.get('encargado','')}", 'description': desc,
                     'start': {'dateTime': apt['start_time'], 'timeZone': 'America/Guayaquil'},
                     'end': {'dateTime': apt['end_time'], 'timeZone': 'America/Guayaquil'},
                     'attendees': attendees, 'reminders': {'useDefault': False, 'overrides': [
                         {'method': 'email', 'minutes': 1440}, {'method': 'popup', 'minutes': 30}]}}
            created = service.events().insert(calendarId='primary', body=event, sendUpdates='all').execute()
            app.supabase.update('appointments', aid, {'status': 'confirmed', 'google_event_id': created.get('id')})
            return {'success': True, 'message': f'✅ Google: {len(attendees)} invitados'}
        except Exception as e:
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    @app.route('/calendar/api/reject/<aid>', methods=['POST'])
    @login_required
    def api_reject(aid):
        app.supabase.update('appointments', aid, {'status': 'cancelled'})
        return {'success': True}

    @app.route('/calendar/api/delete/<aid>', methods=['POST'])
    @login_required
    def api_delete(aid):
        apts = app.supabase.get('appointments', {'id': aid})
        if apts and apts[0].get('google_event_id'):
            creds = get_google_creds(app)
            if creds:
                try: build('calendar', 'v3', credentials=creds).events().delete(calendarId='primary', eventId=apts[0]['google_event_id']).execute()
                except: pass
        app.supabase.delete('appointments', aid)
        return {'success': True}

    @app.route('/health')
    def health(): return {'status': 'ok'}

    return app