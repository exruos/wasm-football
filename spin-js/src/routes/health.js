import { jsonResponse } from '../http.js';

export function registerHealthRoutes(router, { getConnection }) {
    router.get('/health', async () => jsonResponse({ status: 'ok' }));

    router.get('/ready', async () => {
        try {
            await getConnection().query('SELECT 1', []);
            return jsonResponse({ status: 'ok' });
        } catch (err) {
            return jsonResponse({ status: 'db-error', error: String(err) }, 500);
        }
    });
}
