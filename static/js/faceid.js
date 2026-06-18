// ============================================================
//  Reconocimiento facial por cámara (face-api.js)
//  - Detecta el rostro y calcula un descriptor de 128 números
//    en el navegador (la imagen nunca se sube).
//  - El servidor compara el descriptor y decide.
//  Nota: es una conveniencia, SIN detección de vida.
// ============================================================
const FACE_LIB = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/dist/face-api.min.js';
const FACE_MODELS = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model/';
let _faceModelsLoaded = false;
let _faceStream = null;

function _loadScript(src) {
  return new Promise((res, rej) => {
    if (window.faceapi) return res();
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => res();
    s.onerror = () => rej(new Error('No se pudo cargar la librería facial'));
    document.head.appendChild(s);
  });
}

async function _ensureModels(status) {
  await _loadScript(FACE_LIB);
  if (_faceModelsLoaded) return;
  if (status) status('Cargando modelos (solo la 1ª vez)…');
  await faceapi.nets.tinyFaceDetector.loadFromUri(FACE_MODELS);
  await faceapi.nets.faceLandmark68Net.loadFromUri(FACE_MODELS);
  await faceapi.nets.faceRecognitionNet.loadFromUri(FACE_MODELS);
  _faceModelsLoaded = true;
}

function _faceOverlay() {
  let el = document.getElementById('faceOverlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'faceOverlay';
  el.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(15,23,42,.92);' +
    'display:none;flex-direction:column;align-items:center;justify-content:center;padding:20px;';
  el.innerHTML =
    '<div style="background:#fff;border-radius:18px;padding:18px;max-width:380px;width:100%;text-align:center;">' +
    '<h5 style="font-weight:700;margin-bottom:8px;">Reconocimiento facial</h5>' +
    '<video id="faceVideo" autoplay playsinline muted ' +
    'style="width:100%;border-radius:14px;background:#000;aspect-ratio:1/1;object-fit:cover;"></video>' +
    '<div id="faceStatus" style="font-size:.82rem;color:#64748b;margin:10px 0;">Iniciando cámara…</div>' +
    '<div style="display:flex;gap:8px;">' +
    '<button id="faceCancel" type="button" class="btn btn-outline-secondary rounded-pill flex-grow-1">Cancelar</button>' +
    '<button id="faceCapture" type="button" class="btn btn-primary rounded-pill flex-grow-1" disabled>📸 Capturar</button>' +
    '</div></div>';
  document.body.appendChild(el);
  return el;
}

function _stopCamera() {
  if (_faceStream) { _faceStream.getTracks().forEach(t => t.stop()); _faceStream = null; }
}

async function _runFaceFlow(onDescriptor) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert('Este dispositivo o navegador no permite acceso a la cámara.');
    return;
  }
  const overlay = _faceOverlay();
  const video = overlay.querySelector('#faceVideo');
  const statusEl = overlay.querySelector('#faceStatus');
  const btnCap = overlay.querySelector('#faceCapture');
  const btnCancel = overlay.querySelector('#faceCancel');
  const status = (m) => { statusEl.textContent = m; };
  overlay.style.display = 'flex';
  btnCap.disabled = true;

  function close() { _stopCamera(); overlay.style.display = 'none'; }
  btnCancel.onclick = close;

  try {
    _faceStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
    video.srcObject = _faceStream;
  } catch (e) { status('No se pudo abrir la cámara: ' + e.message); return; }

  try { await _ensureModels(status); }
  catch (e) { status('Error cargando modelos: ' + e.message); return; }

  status('Coloca tu rostro dentro del recuadro y presiona Capturar.');
  btnCap.disabled = false;

  btnCap.onclick = async () => {
    btnCap.disabled = true; status('Analizando…');
    let det;
    try {
      det = await faceapi.detectSingleFace(video, new faceapi.TinyFaceDetectorOptions())
        .withFaceLandmarks().withFaceDescriptor();
    } catch (e) { status('Error: ' + e.message); btnCap.disabled = false; return; }
    if (!det) { status('No se detectó un rostro. Mejora la luz y reintenta.'); btnCap.disabled = false; return; }
    const descriptor = Array.from(det.descriptor);
    const ok = await onDescriptor(descriptor, status);
    if (ok !== false) close();
    else btnCap.disabled = false;
  };
}

// ---- Registrar rostro (usuario logueado, en Perfil) ----
async function faceEnroll() {
  await _runFaceFlow(async (descriptor, status) => {
    status('Guardando…');
    try {
      const r = await fetch('/face/enroll', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ descriptor: descriptor, nombre: 'Rostro' })
      });
      const res = await r.json();
      if (res.success) { alert('✅ Rostro registrado'); if (window.faceOnChange) window.faceOnChange(); return true; }
      alert('❌ ' + (res.error || 'No se pudo registrar')); return false;
    } catch (e) { alert('Error de red: ' + e.message); return false; }
  });
}

// ---- Entrar con rostro (en login; requiere email escrito) ----
async function faceLogin() {
  const emailEl = document.querySelector('input[name="email"]');
  const email = emailEl ? emailEl.value.trim().toLowerCase() : '';
  if (!email) { alert('Escribe tu email primero para entrar con rostro.'); if (emailEl) emailEl.focus(); return; }
  await _runFaceFlow(async (descriptor, status) => {
    status('Verificando…');
    try {
      const r = await fetch('/face/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, descriptor: descriptor })
      });
      const res = await r.json();
      if (res.success) { window.location.href = res.redirect || '/dashboard'; return true; }
      alert('❌ ' + (res.error || 'Rostro no reconocido')); return false;
    } catch (e) { alert('Error de red: ' + e.message); return false; }
  });
}
