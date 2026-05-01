
import { createApp } from './app.js';
import { getConnection, parseId } from './db.js';

const router = createApp({ getConnection, parseId });

addEventListener('fetch', (event) => {
    event.respondWith(router.fetch(event.request));
});

