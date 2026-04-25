import { jsonResponse } from '../http';

export function registerHealthRoutes(router) {
    router.get('/health', () => jsonResponse({ status: 'ok' }));
}
