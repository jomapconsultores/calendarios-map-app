import os
import requests

s = requests.Session()

BASE_URL = os.environ.get("APP_BASE_URL", "https://calendario.pensamiento-libre.org")
EMAIL = "test@test.com"
PASSWORD = "test123"

# Iniciar sesión
r = s.post(f'{BASE_URL}/login', data={'email': EMAIL, 'password': PASSWORD}, allow_redirects=False)
print('Login status:', r.status_code)
print('Login headers:', dict(r.headers))
print('Login text:', r.text[:500])

# Si login fue 302, seguimos
if r.status_code == 302:
    # Ver pendientes
    r = s.get(f'{BASE_URL}/calendar/api/pending')
    print('\nPendientes status:', r.status_code)
    print('Pendientes text:', r.text[:500])
else:
    print('\n❌ Login falló. Verifica email/contraseña.')