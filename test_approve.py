import requests

s = requests.Session()

EMAIL = "test@test.com"
PASSWORD = "test123"

# Iniciar sesión
r = s.post('https://calendarios-map.onrender.com/login', data={'email': EMAIL, 'password': PASSWORD}, allow_redirects=False)
print('Login status:', r.status_code)
print('Login headers:', dict(r.headers))
print('Login text:', r.text[:500])

# Si login fue 302, seguimos
if r.status_code == 302:
    # Ver pendientes
    r = s.get('https://calendarios-map.onrender.com/calendar/api/pending')
    print('\nPendientes status:', r.status_code)
    print('Pendientes text:', r.text[:500])
else:
    print('\n❌ Login falló. Verifica email/contraseña.')