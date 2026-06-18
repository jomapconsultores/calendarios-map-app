// ============================================================
//  WebAuthn cliente — Face ID / huella (passkeys)
//  Convierte base64url <-> ArrayBuffer y maneja registro/login.
//  Compatible con py_webauthn en el backend.
// ============================================================
const wa = {
  b64urlToBuf(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    const bin = atob(s), buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
  },
  bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  },
  supported() {
    return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
  }
};

// ---- Registrar este dispositivo (usuario ya logueado) ----
async function waRegister() {
  if (!wa.supported()) {
    alert('Este dispositivo o navegador no soporta Face ID / huella (WebAuthn).');
    return;
  }
  let options;
  try {
    const r = await fetch('/webauthn/register/begin', { method: 'POST' });
    options = await r.json();
    if (options.error) { alert('Error: ' + options.error); return; }
  } catch (e) { alert('Error de red: ' + e.message); return; }

  options.challenge = wa.b64urlToBuf(options.challenge);
  options.user.id = wa.b64urlToBuf(options.user.id);
  if (options.excludeCredentials) options.excludeCredentials.forEach(c => c.id = wa.b64urlToBuf(c.id));

  let cred;
  try { cred = await navigator.credentials.create({ publicKey: options }); }
  catch (e) { alert('No se pudo registrar: ' + e.message); return; }

  const nombre = prompt('Nombre para este dispositivo (ej. iPhone de Marco):', '') || 'Dispositivo';
  const payload = {
    id: cred.id,
    rawId: wa.bufToB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment || undefined,
    response: {
      attestationObject: wa.bufToB64url(cred.response.attestationObject),
      clientDataJSON: wa.bufToB64url(cred.response.clientDataJSON),
      transports: cred.response.getTransports ? cred.response.getTransports() : []
    },
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
    nombre: nombre
  };
  try {
    const r = await fetch('/webauthn/register/complete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const res = await r.json();
    if (res.success) {
      alert('✅ Face ID / huella activado en este dispositivo');
      if (window.waOnChange) window.waOnChange();
    } else {
      alert('❌ ' + (res.error || 'No se pudo activar'));
    }
  } catch (e) { alert('Error de red: ' + e.message); }
}

// ---- Iniciar sesión con Face ID / huella (sin escribir email) ----
async function waLogin() {
  if (!wa.supported()) {
    alert('Este dispositivo o navegador no soporta Face ID / huella.');
    return;
  }
  let options;
  try {
    const r = await fetch('/webauthn/authenticate/begin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    options = await r.json();
    if (options.error) { alert('Error: ' + options.error); return; }
  } catch (e) { alert('Error de red: ' + e.message); return; }

  options.challenge = wa.b64urlToBuf(options.challenge);
  if (options.allowCredentials) options.allowCredentials.forEach(c => c.id = wa.b64urlToBuf(c.id));

  let assertion;
  try { assertion = await navigator.credentials.get({ publicKey: options }); }
  catch (e) { alert('No se pudo autenticar: ' + e.message); return; }

  const payload = {
    id: assertion.id,
    rawId: wa.bufToB64url(assertion.rawId),
    type: assertion.type,
    response: {
      authenticatorData: wa.bufToB64url(assertion.response.authenticatorData),
      clientDataJSON: wa.bufToB64url(assertion.response.clientDataJSON),
      signature: wa.bufToB64url(assertion.response.signature),
      userHandle: assertion.response.userHandle ? wa.bufToB64url(assertion.response.userHandle) : null
    },
    clientExtensionResults: assertion.getClientExtensionResults ? assertion.getClientExtensionResults() : {}
  };
  try {
    const r = await fetch('/webauthn/authenticate/complete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const res = await r.json();
    if (res.success) window.location.href = res.redirect || '/dashboard';
    else alert('❌ ' + (res.error || 'No se pudo iniciar sesión'));
  } catch (e) { alert('Error de red: ' + e.message); }
}
