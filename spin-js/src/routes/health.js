import { jsonResponse } from '../http.js';

export function registerHealthRoutes(router, dependencies) {
    router.get('/health', () => jsonResponse({ status: 'ok' }));

    if (dependencies && typeof dependencies.getConnection === 'function') {
        router.get('/ready', async () => {
            try {
                const conn = dependencies.getConnection();
                await conn.query('SELECT 1');
                return jsonResponse({ status: 'ok' });
            } catch (err) {
                return jsonResponse({ status: 'db-error', error: String(err) }, 500);
            }
        });
    }
}
