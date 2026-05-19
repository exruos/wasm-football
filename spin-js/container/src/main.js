import { createServer } from 'node:http';
import { createApp } from '../../src/app.js';
import { getConnection, parseId } from './db.js';

const router = createApp({ getConnection, parseId });
const port = Number.parseInt(process.env.PORT ?? '8080', 10);
const host = process.env.HOST ?? '0.0.0.0';

const server = createServer(async (request, response) => {
    try {
        const requestUrl = new URL(request.url ?? '/', `http://${request.headers.host ?? `${host}:${port}`}`);
        const fetchRequest = new Request(requestUrl, {
            method: request.method,
            headers: request.headers,
        });

        const fetchResponse = await router.fetch(fetchRequest);
        const body = await fetchResponse.text();

        response.statusCode = fetchResponse.status;
        fetchResponse.headers.forEach((value, name) => {
            response.setHeader(name, value);
        });
        response.end(body);
    } catch (error) {
        response.statusCode = 500;
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ error: error instanceof Error ? error.message : 'Internal Server Error' }));
    }
});

server.listen(port, host, () => {
    console.log(`football-js container listening on http://${host}:${port}`);
});