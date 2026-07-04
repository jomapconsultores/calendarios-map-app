import requests

s = requests.Session()

EMAIL = "test@test.com"
PASSWORD = "test123"

# Iniciar sesión
print('Login status:', r.status_code)
print('Login headers:', dict(r.headers))
print('Login text:', r.text[:500])

# Si login fue 302, seguimos
if r.status_code == 302:
    # Ver pendientes
    print('\nPendientes status:', r.status_code)
    print('Pendientes text:', r.text[:500])
else:
    print('\n❌ Login falló. Verifica email/contraseña.')