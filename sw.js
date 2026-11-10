self.addEventListener('install', (event) => {
    console.log('Service Worker installed');
    self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
    // Basic fetch handler to prevent errors
});
