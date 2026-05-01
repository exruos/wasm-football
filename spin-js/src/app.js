import { AutoRouter } from 'itty-router';
import { registerHealthRoutes } from './routes/health.js';
import { registerMatchRoutes } from './routes/match.js';
import { registerPlayerRoutes } from './routes/players.js';
import { registerTeamRoutes } from './routes/teams.js';

export function createApp(dependencies) {
    const router = AutoRouter();

    registerHealthRoutes(router);
    registerPlayerRoutes(router, dependencies);
    registerTeamRoutes(router, dependencies);
    registerMatchRoutes(router, dependencies);

    return router;
}