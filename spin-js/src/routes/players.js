import { jsonResponse, notFound } from '../http.js';
import { playerAttributesFromRow, playerFromRow } from '../mappers.js';

const SELECT_PLAYER_BY_ID =
    'SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, height::int as height, weight::int as weight FROM player WHERE id = $1';

export function registerPlayerRoutes(router, { getConnection, parseId }) {
    router.get('/players/record/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const rowSet = await connection.query(SELECT_PLAYER_BY_ID, [parseId(id)]);

            if (!rowSet.rows.length) {
                return notFound();
            }

            const player = playerFromRow(rowSet.rows[0]);
            const attributesRowSet = await connection.query(
                'SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2',
                [player.apiId, player.fifaApiId],
            );

            const attributes = attributesRowSet.rows.map(playerAttributesFromRow);
            return jsonResponse({ player, attributes });
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });

    router.get('/players/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const rowSet = await connection.query(SELECT_PLAYER_BY_ID, [parseId(id)]);

            if (!rowSet.rows.length) {
                return notFound();
            }

            return jsonResponse(playerFromRow(rowSet.rows[0]));
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });
}
