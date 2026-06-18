// ============================================================
//  Service Worker — calendarios-map PWA
//  - Navegaciones: network-first con fallback a /offline.html
//  - Estáticos (/static/): cache-first
//  - APIs y POST: siempre red (nunca se cachean datos sensibles)
// ============================================================
const CACHE = 'calmap-v1';
const PRECACHE = [
  '/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return; // dejar pasar CDNs / Google sin tocar

  // Navegaciones (abrir páginas): primero red, si no hay conexión -> offline
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
    return;
  }

  // Archivos estáticos: primero caché, luego red (y se guarda)
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }
  // El resto (APIs): comportamiento por defecto (red)
});
