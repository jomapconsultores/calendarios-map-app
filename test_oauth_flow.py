#!/usr/bin/env python3
import requests
from urllib.parse import urlparse, parse_qs
import json

BASE_URL = 'http://127.0.0.1:5000'
session = requests.Session()

print("=" * 60)
print("TESTING GOOGLE OAUTH FLOW")
print("=" * 60)

# Step 1: Register a test user
print("\n[1] Registering test user...")
register_data = {
    'email': 'oauth_test@example.com',
    'password': 'test123456',
    'full_name': 'OAuth Test User',
    'calendars': []
}
r = session.post(f'{BASE_URL}/register', data=register_data)
print(f"✓ Registration: {r.status_code}")

# Step 2: Login with test user
print("\n[2] Logging in with test user...")
login_data = {
    'email': 'oauth_test@example.com',
    'password': 'test123456'
}
r = session.post(f'{BASE_URL}/login', data=login_data)
print(f"✓ Login: {r.status_code}")
if r.status_code == 200 and 'Login' in r.text:
    print("❌ Login failed - still on login page")
else:
    print(f"✓ Login successful - redirected to dashboard")

# Step 3: Access dashboard to verify logged in
print("\n[3] Accessing dashboard...")
r = session.get(f'{BASE_URL}/dashboard')
print(f"✓ Dashboard: {r.status_code}")
if 'dashboard' in r.text or 'logout' in r.text.lower():
    print("✓ User is authenticated")
else:
    print("❌ User is not authenticated")

# Step 4: Initiate Google OAuth
print("\n[4] Initiating Google OAuth...")
r = session.get(f'{BASE_URL}/auth/google', allow_redirects=False)
print(f"✓ OAuth initiate: {r.status_code}")
if r.status_code in [302, 301]:
    google_url = r.headers.get('Location', '')
    print(f"✓ Redirect URL obtained: {google_url[:80]}...")

    # Parse the URL to get the state
    parsed = urlparse(google_url)
    params = parse_qs(parsed.query)
    state = params.get('state', [None])[0]

    if state:
        print(f"✓ State token obtained: {state[:20]}...")

        # Step 5: Check if state is stored in session
        print("\n[5] Verifying session state...")
        r = session.get(f'{BASE_URL}/dashboard')
        if 'dashboard' in r.text:
            print("✓ Session maintained after OAuth initiation")
        else:
            print("❌ Session lost")
    else:
        print("❌ No state token in redirect URL")
else:
    print(f"❌ Unexpected status code: {r.status_code}")

print("\n[6] Testing /health endpoint...")
r = session.get(f'{BASE_URL}/health')
print(f"✓ Health check: {r.status_code}")
print(f"  Response: {r.json()}")

print("\n" + "=" * 60)
print("OAUTH FLOW TEST COMPLETED")
print("=" * 60)
