const CACHE_NAME = 'scout-v2';
const ASSETS = [
  'index.html',
  'assistant.html',
  'placements.html',
  'study.html',
  'simulator.html',
  'settings.html',
  'ollama.css',
  'scout-sidebar.js',
  'logo.png',
  'manifest.json'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(ASSETS);
    })
  );
});

self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(response => {
      return response || fetch(e.request);
    })
  );
});
