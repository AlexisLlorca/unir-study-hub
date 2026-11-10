// Service Worker para soporte PWA e instalación en pantalla de inicio (Android, iOS, PC)
const CACHE_NAME = 'nova-study-hub-pwa-v2';
const PRECACHE_URLS = [
  '/',
  '/manifest.json',
  '/static/pwa-icon-192.png',
  '/static/pwa-icon-512.png',
  '/static/pwa-icon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) return caches.delete(key);
        })
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // No interceptar peticiones a la API para asegurar datos siempre en tiempo real
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.status === 200 && (url.pathname.startsWith('/static/') || url.pathname.includes('font') || url.pathname.includes('cdn'))) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((res) => res || caches.match('/')))
  );
});