// Mentis service worker: installable PWA + team call ringing.
// v2026-11-27-plain-chat
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

const RING_MS = 45000;

async function appIsVisible() {
  const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
  return all.some((c) => c.visibilityState === 'visible');
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}

  event.waitUntil((async () => {
    // The open page already rings by itself, so don't ring twice.
    if (data.type === 'incoming' && await appIsVisible()) return;

    if (data.type === 'missed') {
      // Replaces the ringing notification (same tag) with a quiet "missed call".
      await self.registration.showNotification(data.title || 'Missed team call', {
        body: data.body || 'You missed a team call',
        icon: '/static/images/icon-192.png',
        badge: '/static/images/icon-192.png',
        tag: data.tag || 'team-call',
        data: { url: data.url || '/team-chat' },
      });
      return;
    }

    const tag = data.tag || 'team-call';
    await self.registration.showNotification(data.title || 'Mentis team call', {
      body: data.body || 'Incoming team call',
      icon: '/static/images/icon-192.png',
      badge: '/static/images/icon-192.png',
      tag,
      renotify: true,
      requireInteraction: true,
      vibrate: [500, 250, 500, 250, 500, 250, 500],
      data: { url: data.url || '/team-chat', inviteId: data.inviteId || null },
      actions: [
        { action: 'answer', title: 'Answer' },
        { action: 'decline', title: 'Decline' },
      ],
    });

    // A ring should not hang around forever: close it when the ring time is over.
    await new Promise((resolve) => setTimeout(resolve, RING_MS));
    const still = await self.registration.getNotifications({ tag });
    still.forEach((n) => { if (n.actions && n.actions.length) n.close(); });
  })());
});

self.addEventListener('notificationclick', (event) => {
  const n = event.notification;
  const info = n.data || {};
  n.close();

  if (event.action === 'decline' && info.inviteId) {
    event.waitUntil(fetch('/api/team/call/respond', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inviteId: info.inviteId, action: 'decline' }),
    }).catch(() => {}));
    return;
  }

  const url = info.url || '/team-chat';
  event.waitUntil((async () => {
    const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of all) {
      if ('focus' in client) {
        try { await client.navigate(url); } catch (_) {}
        return client.focus();
      }
    }
    if (clients.openWindow) return clients.openWindow(url);
  })());
});

self.addEventListener('fetch', (event) => {
  // Pass everything straight through to the network for now.
  event.respondWith(fetch(event.request));
});
