// ============================================================
//  Service Worker â€” calendarios-map PWA
//  - Navegaciones: network-first con fallback a /offline.html
//  - EstÃ¡ticos (/static/): cache-first
//  - APIs y POST: siempre red (nunca se cachean datos sensibles)
// ============================================================
// Subir esta versiÃ³n invalida TODA la cachÃ© anterior. Hay que hacerlo en cada
// despliegue que cambie CSS o JS: si no, el telÃ©fono sigue sirviendo los
// archivos viejos que guardÃ³ y el usuario ve la versiÃ³n anterior sin saberlo.
// v2: se retiraron Kanban/Gantt/mapa de procesos y entraron movil.css e
//     instalar.js â€” la cachÃ© v1 aÃºn tenÃ­a los archivos antiguos.
// v4: la tipografÃ­a dejÃ³ de pedirse con @import dentro de style.css y pasÃ³ al
//     <head>. Sin subir esta versiÃ³n, el telÃ©fono seguirÃ­a sirviendo la hoja
//     vieja â€”la del @importâ€” y en la aplicaciÃ³n instalada no se verÃ­a nada del
//     cambio.
// v5: barra superior (la identidad ya no se cae de fila) e instalar.js (el
//     aviso de instalaciÃ³n sÃ³lo se ofrece en dispositivos de mano).
const CACHE = 'calmap-v11';
// SÃ³lo lo que se pide SIN parÃ¡metro de versiÃ³n. El CSS y el JS se enlazan con
// Â«?v=NÂ», y la cachÃ© distingue por URL completa: precargarlos aquÃ­ sin el
// parÃ¡metro no servirÃ­a de nada (nunca coincidirÃ­a con lo que pide la pÃ¡gina) y
// ademÃ¡s el Â«?v=NÂ» es justo lo que fuerza a bajar la versiÃ³n nueva.
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

  // Navegaciones (abrir pÃ¡ginas): primero red, si no hay conexiÃ³n -> offline
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
    return;
  }

  // Archivos estÃ¡ticos: primero cachÃ©, luego red (y se guarda)
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          // Solo se guarda lo que llegÃ³ bien: sin esta comprobaciÃ³n, un 404 o
          // un 500 se quedaban en cachÃ© y se seguÃ­an sirviendo hasta el
          // siguiente cambio de versiÃ³n, aunque el archivo ya estuviera bien.
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
  try { data = e.data ? e.data.json() : {}; } catch (err) { data = { title: 'NotificaciÃ³n', body: (e.data && e.data.text()) || '' }; }
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

