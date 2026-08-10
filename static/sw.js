// ============================================================
//  Service Worker — calendarios-map PWA
//  - Navegaciones: network-first con fallback a /offline.html
//  - Estáticos (/static/): cache-first
//  - APIs y POST: siempre red (nunca se cachean datos sensibles)
// ============================================================
// Subir esta versión invalida TODA la caché anterior. Hay que hacerlo en cada
// despliegue que cambie CSS o JS: si no, el teléfono sigue sirviendo los
// archivos viejos que guardó y el usuario ve la versión anterior sin saberlo.
// v2: se retiraron Kanban/Gantt/mapa de procesos y entraron movil.css e
//     instalar.js — la caché v1 aún tenía los archivos antiguos.
const CACHE = 'calmap-v2';
// Sólo lo que se pide SIN parámetro de versión. El CSS y el JS se enlazan con
// «?v=N», y la caché distingue por URL completa: precargarlos aquí sin el
// parámetro no serviría de nada (nunca coincidiría con lo que pide la página) y
// además el «?v=N» es justo lo que fuerza a bajar la versión nueva.
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
          // Solo se guarda lo que llegó bien: sin esta comprobación, un 404 o
          // un 500 se quedaban en caché y se seguían sirviendo hasta el
          // siguiente cambio de versión, aunque el archivo ya estuviera bien.
          if (res.ok && res.type === 'basic') {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }
  // El resto (APIs): comportamiento por defecto (red)
});

// ============================================================
//  PUSH NOTIFICATIONS
// ============================================================
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) { data = { title: 'Notificación', body: (e.data && e.data.text()) || '' }; }
  const title = data.title || 'calendarios-map';
  const options = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/dashboard' },
    vibrate: [200, 100, 200],
    tag: data.tag || 'calmap',
    renotify: true,
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || '/dashboard';
  e.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((winList) => {
      for (const c of winList) {
        if (c.url.includes(target) && 'focus' in c) return c.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
