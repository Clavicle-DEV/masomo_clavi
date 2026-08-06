// Minimal service worker — required by browsers to treat Clavis as an
// installable app. Deliberately does not cache aggressively yet (the app
// relies on live data), it just needs to exist and handle fetch to satisfy
// installability checks.
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Pass everything straight through to the network for now.
  event.respondWith(fetch(event.request));
});
