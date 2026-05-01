import { jsonResponse } from '../http.js';

export function registerHealthRoutes(router) {
    router.get('/health', () => jsonResponse({ status: 'ok' }));
}
