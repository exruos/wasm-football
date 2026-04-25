
import { AutoRouter } from 'itty-router';
import { registerHealthRoutes } from './routes/health';
import { registerMatchRoutes } from './routes/match';
import { registerPlayerRoutes } from './routes/players';
import { registerTeamRoutes } from './routes/teams';

const router = AutoRouter();

registerHealthRoutes(router);
registerPlayerRoutes(router);
registerTeamRoutes(router);
registerMatchRoutes(router);

addEventListener('fetch', (event) => {
    event.respondWith(router.fetch(event.request));
});

