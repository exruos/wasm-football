import { jsonResponse, notFound, textResponse } from '../http.js';
import { matchDtoFromRow, toMatchResource } from '../mappers.js';
import { buildResultTable } from '../result-table.js';

const REQUIRED_RESULT_TABLE_QUERY_ERROR = 'Missing required query parameters: season and leagueName';

const SELECT_MATCH_RESOURCE_FIELDS =
    'SELECT id, country_id, league_id, season, stage, date::text as date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match';

async function resolveTeamName(connection, teamId) {
    const rowSet = await connection.query('SELECT team_long_name FROM team WHERE team_api_id = $1', [teamId]);
    const row = rowSet.rows[0];
    return row?.team_long_name ?? 'Unknown';
}

export function registerMatchRoutes(router, { getConnection, parseId }) {
    router.get('/match/result-table', async (request) => {
        const url = new URL(request.url);
        const season = url.searchParams.get('season');
        const leagueName = url.searchParams.get('leagueName');

        if (!season || !leagueName) {
            return textResponse(REQUIRED_RESULT_TABLE_QUERY_ERROR, 400);
        }

        const connection = getConnection();
        const rowSet = await connection.query(
            'SELECT m.home_team_api_id, m.away_team_api_id, m.home_team_goal, m.away_team_goal FROM match m JOIN league l ON m.league_id = l.id WHERE m.season = $1 AND l.name = $2',
            [season, leagueName],
        );

        const matches = rowSet.rows.map((row) => ({
            homeTeamId: row.home_team_api_id,
            awayTeamId: row.away_team_api_id,
            homeTeamGoal: row.home_team_goal,
            awayTeamGoal: row.away_team_goal,
        }));

        const resultTable = [];
        for (const row of buildResultTable(matches)) {
            resultTable.push({
                teamId: row.teamId,
                teamName: await resolveTeamName(connection, row.teamId),
                points: row.points,
                wins: row.wins,
                draws: row.draws,
                losses: row.losses,
                goalsScored: row.goalsScored,
                goalsConceded: row.goalsConceded,
            });
        }

        return jsonResponse(resultTable);
    });

    router.get('/match/team/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const parsedId = parseId(id);
            const rowSet = await connection.query(
                `${SELECT_MATCH_RESOURCE_FIELDS} WHERE home_team_api_id = $1 OR away_team_api_id = $1`,
                [parsedId],
            );

            if (!rowSet.rows.length) {
                return notFound();
            }

            const resources = [];
            for (const row of rowSet.rows) {
                const matchDto = matchDtoFromRow(row);
                const homeTeamName = await resolveTeamName(connection, matchDto.homeTeamApiId ?? 0);
                const awayTeamName = await resolveTeamName(connection, matchDto.awayTeamApiId ?? 0);
                resources.push(toMatchResource(matchDto, homeTeamName, awayTeamName));
            }

            return jsonResponse(resources);
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });

    router.get('/match/:id', async ({ id }) => {
        try {
            const connection = getConnection();
            const rowSet = await connection.query(`${SELECT_MATCH_RESOURCE_FIELDS} WHERE id = $1`, [parseId(id)]);

            if (!rowSet.rows.length) {
                return notFound();
            }

            const matchDto = matchDtoFromRow(rowSet.rows[0]);
            const homeTeamName = await resolveTeamName(connection, matchDto.homeTeamApiId ?? 0);
            const awayTeamName = await resolveTeamName(connection, matchDto.awayTeamApiId ?? 0);

            return jsonResponse(toMatchResource(matchDto, homeTeamName, awayTeamName));
        } catch (error) {
            if (error.status === 400) {
                return jsonResponse({ status: 400, error: error.message }, 400);
            }
            throw error;
        }
    });
}
