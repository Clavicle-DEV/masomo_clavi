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

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  event.waitUntil(
    self.registration.showNotification(data.title || 'Clavis team call', {
      body: data.body || 'Incoming team call',
      icon: '/static/images/icon-192.png',
      badge: '/static/images/icon-192.png',
      tag: data.tag || 'team-call',
      vibrate: [300, 150, 300, 150, 300],
      requireInteraction: true,
      data: { url: data.url || '/team-chat' },
      actions: [
        { action: 'open', title: 'Answer' },
        { action: 'close', title: 'Decline' }
      ],
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'close') return;
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : '/team-chat';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((matched) => {
    for (const client of matched) {
      if ('focus' in client) { client.navigate(url); return client.focus(); }
    }
    if (clients.openWindow) return clients.openWindow(url);
  }));
});

self.addEventListener('fetch', (event) => {
  // Pass everything straight through to the network for now.
  event.respondWith(fetch(event.request));
});
