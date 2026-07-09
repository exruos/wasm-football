import { jsonResponse, notFound } from '../http.js';
import { teamAttributesFromRow, teamFromRow } from '../mappers.js';

export function registerTeamRoutes(router, { getConnection, parseId }) {
    router.get('/teams/api-id/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const rowSet = await connection.query('SELECT * FROM team WHERE team_api_id = $1', [parseId(id)]);

            if (!rowSet.rows.length) {
                return notFound();
            }

            return jsonResponse(teamFromRow(rowSet.rows[0]));
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });

    router.get('/teams/record/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const teamRowSet = await connection.query('SELECT * FROM team WHERE id = $1', [parseId(id)]);

            if (!teamRowSet.rows.length) {
                return notFound();
            }

            const team = teamFromRow(teamRowSet.rows[0]);
            const attributesRowSet = await connection.query(
                'SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2',
                [team.apiId, team.fifaApiId],
            );

            const attributes = attributesRowSet.rows.map(teamAttributesFromRow);
            return jsonResponse({ team, attributes });
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });

    router.get('/teams/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const rowSet = await connection.query('SELECT * FROM team WHERE id = $1', [parseId(id)]);

            if (!rowSet.rows.length) {
                return notFound();
            }

            return jsonResponse(teamFromRow(rowSet.rows[0]));
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });
}
