import { getConnection, parseId } from '../db';
import { jsonResponse, notFound } from '../http';
import { teamAttributesFromRow, teamFromRow } from '../mappers';

export function registerTeamRoutes(router) {
    router.get('/teams/api-id/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query('SELECT * FROM team WHERE team_api_id = $1', [parseId(id)]);

        if (!rowSet.rows.length) {
            return notFound();
        }

        return jsonResponse(teamFromRow(rowSet.rows[0]));
    });

    router.get('/teams/record/:id', ({ id }) => {
        const connection = getConnection();
        const teamRowSet = connection.query('SELECT * FROM team WHERE team_api_id = $1', [parseId(id)]);

        if (!teamRowSet.rows.length) {
            return notFound();
        }

        const team = teamFromRow(teamRowSet.rows[0]);
        const attributesRowSet = connection.query(
            'SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2',
            [team.teamApiId, team.teamFifaApiId],
        );

        const attributes = attributesRowSet.rows.map(teamAttributesFromRow);
        return jsonResponse({ team, attributes });
    });

    router.get('/teams/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query('SELECT * FROM team WHERE id = $1', [parseId(id)]);

        if (!rowSet.rows.length) {
            return notFound();
        }

        return jsonResponse(teamFromRow(rowSet.rows[0]));
    });
}
