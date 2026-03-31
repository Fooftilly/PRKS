/* Minimal service worker for PWA installability (Chrome requires a fetch handler). */

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Empty handler: satisfies installability (Chrome expects a fetch listener) without proxying
// every request through fetch(), which amplified manifest/icon revalidation traffic.
self.addEventListener('fetch', () => {});
