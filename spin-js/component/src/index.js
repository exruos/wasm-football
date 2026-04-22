
import { AutoRouter } from 'itty-router';
import { open } from '@spinframework/spin-postgres';
import { get as getVariable } from '@spinframework/spin-variables';

const router = AutoRouter();

const DB_URL_DEFAULT = 'postgres://postgres:postgres@localhost:5438/postgres';
const REQUIRED_RESULT_TABLE_QUERY_ERROR = 'Missing required query parameters: season and leagueName';
const POINTS_FOR_WIN = 3;
const POINTS_FOR_DRAW = 1;

function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            'content-type': 'application/json',
        },
    });
}

function textResponse(message, status) {
    return new Response(message, { status });
}

function notFound() {
    return new Response(null, { status: 404 });
}

function getConnection() {
    const address = getVariable('db_url') ?? DB_URL_DEFAULT;
    return open(address);
}

function parseId(rawId) {
    const parsed = Number.parseInt(rawId ?? '', 10);
    return Number.isNaN(parsed) ? 0 : parsed;
}

function formatDate(value) {
    if (!value) {
        return '';
    }

    if (typeof value !== 'string') {
        return '';
    }

    return value.split(' ')[0] ?? '';
}

function playerFromRow(row) {
    return {
        id: row.id,
        apiId: row.player_api_id,
        fifaApiId: row.player_fifa_api_id,
        name: row.player_name,
        birthday: formatDate(row.birthday),
        height: row.height,
        weight: row.weight,
    };
}

function playerAttributesFromRow(row) {
    return {
        date: formatDate(row.date),
        overallRating: row.overall_rating,
        potential: row.potential,
        preferredFoot: row.preferred_foot,
        attackingWorkRate: row.attacking_work_rate,
        defensiveWorkRate: row.defensive_work_rate,
        crossing: row.crossing,
        finishing: row.finishing,
        headingAccuracy: row.heading_accuracy,
        shortPassing: row.short_passing,
        volleys: row.volleys,
        dribbling: row.dribbling,
        curve: row.curve,
        freeKickAccuracy: row.free_kick_accuracy,
        longPassing: row.long_passing,
        ballControl: row.ball_control,
        acceleration: row.acceleration,
        sprintSpeed: row.sprint_speed,
        agility: row.agility,
        reactions: row.reactions,
        balance: row.balance,
        shotPower: row.shot_power,
        jumping: row.jumping,
        stamina: row.stamina,
        strength: row.strength,
        longShots: row.long_shots,
        aggression: row.aggression,
        interceptions: row.interceptions,
        positioning: row.positioning,
        vision: row.vision,
        penalties: row.penalties,
        marking: row.marking,
        standingTackle: row.standing_tackle,
        slidingTackle: row.sliding_tackle,
        gkDiving: row.gk_diving,
        gkHandling: row.gk_handling,
        gkKicking: row.gk_kicking,
        gkPositioning: row.gk_positioning,
        gkReflexes: row.gk_reflexes,
    };
}

function teamFromRow(row) {
    return {
        id: row.id,
        teamApiId: row.team_api_id,
        teamFifaApiId: row.team_fifa_api_id,
        teamLongName: row.team_long_name,
        teamShortName: row.team_short_name,
    };
}

function teamAttributesFromRow(row) {
    return {
        id: row.id,
        teamFifaApiId: row.team_fifa_api_id,
        teamApiId: row.team_api_id,
        date: formatDate(row.date),
        buildUpPlaySpeed: row.buildupplayspeed,
        buildUpPlaySpeedClass: row.buildupplayspeedclass,
        buildUpPlayDribbling: row.buildupplaydribbling,
        buildUpPlayDribblingClass: row.buildupplaydribblingclass,
        buildUpPlayPassing: row.buildupplaypassing,
        buildUpPlayPassingClass: row.buildupplaypassingclass,
        buildUpPlayPositioningClass: row.buildupplaypositioningclass,
        chanceCreationPassing: row.chancecreationpassing,
        chanceCreationPassingClass: row.chancecreationpassingclass,
        chanceCreationCrossing: row.chancecreationcrossing,
        chanceCreationCrossingClass: row.chancecreationcrossingclass,
        chanceCreationShooting: row.chancecreationshooting,
        chanceCreationShootingClass: row.chancecreationshootingclass,
        chanceCreationPositioningClass: row.chancecreationpositioningclass,
        defencePressure: row.defencepressure,
        defencePressureClass: row.defencepressureclass,
        defenceAggression: row.defenceaggression,
        defenceAggressionClass: row.defenceaggressionclass,
        defenceTeamWidth: row.defenceteamwidth,
        defenceTeamWidthClass: row.defenceteamwidthclass,
        defenceDefenderLineClass: row.defencedefenderlineclass,
    };
}

function matchDtoFromRow(row) {
    return {
        id: row.id,
        countryId: row.country_id,
        leagueId: row.league_id,
        season: row.season,
        stage: row.stage,
        date: formatDate(row.date),
        matchApiId: row.match_api_id,
        homeTeamApiId: row.home_team_api_id,
        awayTeamApiId: row.away_team_api_id,
        homeTeamGoal: row.home_team_goal,
        awayTeamGoal: row.away_team_goal,
        homePlayerX1: row.home_player_x1,
        homePlayerX2: row.home_player_x2,
        homePlayerX3: row.home_player_x3,
        homePlayerX4: row.home_player_x4,
        homePlayerX5: row.home_player_x5,
        homePlayerX6: row.home_player_x6,
        homePlayerX7: row.home_player_x7,
        homePlayerX8: row.home_player_x8,
        homePlayerX9: row.home_player_x9,
        homePlayerX10: row.home_player_x10,
        homePlayerX11: row.home_player_x11,
        awayPlayerX1: row.away_player_x1,
        awayPlayerX2: row.away_player_x2,
        awayPlayerX3: row.away_player_x3,
        awayPlayerX4: row.away_player_x4,
        awayPlayerX5: row.away_player_x5,
        awayPlayerX6: row.away_player_x6,
        awayPlayerX7: row.away_player_x7,
        awayPlayerX8: row.away_player_x8,
        awayPlayerX9: row.away_player_x9,
        awayPlayerX10: row.away_player_x10,
        awayPlayerX11: row.away_player_x11,
    };
}

function toMatchResource(matchDto, homeTeamName, awayTeamName) {
    return {
        matchId: matchDto.id,
        countryId: matchDto.countryId ?? 0,
        leagueId: matchDto.leagueId ?? 0,
        season: matchDto.season ?? '',
        stage: matchDto.stage ?? 0,
        date: matchDto.date ?? '',
        matchApiId: matchDto.matchApiId ?? 0,
        homeTeamId: matchDto.homeTeamApiId ?? 0,
        awayTeamId: matchDto.awayTeamApiId ?? 0,
        homeTeamName,
        awayTeamName,
        homeTeamGoal: matchDto.homeTeamGoal,
        awayTeamGoal: matchDto.awayTeamGoal,
        homePlayerLineup: {
            player1: matchDto.homePlayerX1,
            player2: matchDto.homePlayerX2,
            player3: matchDto.homePlayerX3,
            player4: matchDto.homePlayerX4,
            player5: matchDto.homePlayerX5,
            player6: matchDto.homePlayerX6,
            player7: matchDto.homePlayerX7,
            player8: matchDto.homePlayerX8,
            player9: matchDto.homePlayerX9,
            player10: matchDto.homePlayerX10,
            player11: matchDto.homePlayerX11,
        },
        awayPlayerLineup: {
            player1: matchDto.awayPlayerX1,
            player2: matchDto.awayPlayerX2,
            player3: matchDto.awayPlayerX3,
            player4: matchDto.awayPlayerX4,
            player5: matchDto.awayPlayerX5,
            player6: matchDto.awayPlayerX6,
            player7: matchDto.awayPlayerX7,
            player8: matchDto.awayPlayerX8,
            player9: matchDto.awayPlayerX9,
            player10: matchDto.awayPlayerX10,
            player11: matchDto.awayPlayerX11,
        },
    };
}

function resolveTeamName(connection, teamId) {
    const rowSet = connection.query(
        'SELECT team_long_name FROM team WHERE team_api_id = $1',
        [teamId],
    );
    const row = rowSet.rows[0];
    return row?.team_long_name ?? 'Unknown';
}

function buildResultTable(matches) {
    const teamStats = new Map();

    const recordMatch = (teamId, scored, conceded) => {
        const current = teamStats.get(teamId) ?? {
            points: 0,
            wins: 0,
            draws: 0,
            losses: 0,
            goalsScored: 0,
            goalsConceded: 0,
        };

        current.goalsScored += scored;
        current.goalsConceded += conceded;

        if (scored > conceded) {
            current.wins += 1;
            current.points += POINTS_FOR_WIN;
        } else if (scored === conceded) {
            current.draws += 1;
            current.points += POINTS_FOR_DRAW;
        } else {
            current.losses += 1;
        }

        teamStats.set(teamId, current);
    };

    for (const match of matches) {
        if (match.homeTeamGoal == null || match.awayTeamGoal == null) {
            continue;
        }

        recordMatch(match.homeTeamId, match.homeTeamGoal, match.awayTeamGoal);
        recordMatch(match.awayTeamId, match.awayTeamGoal, match.homeTeamGoal);
    }

    return [...teamStats.entries()]
        .map(([teamId, stats]) => ({
            teamId,
            points: stats.points,
            wins: stats.wins,
            draws: stats.draws,
            losses: stats.losses,
            goalsScored: stats.goalsScored,
            goalsConceded: stats.goalsConceded,
        }))
        .sort((a, b) => {
            if (b.points !== a.points) {
                return b.points - a.points;
            }

            const goalDiffA = a.goalsScored - a.goalsConceded;
            const goalDiffB = b.goalsScored - b.goalsConceded;
            if (goalDiffB !== goalDiffA) {
                return goalDiffB - goalDiffA;
            }

            return b.goalsScored - a.goalsScored;
        });
}

router
    .get('/health', () => jsonResponse({ status: 'ok' }))
    .get('/players/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query(
            'SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, height::int as height, weight::int as weight FROM player WHERE id = $1',
            [parseId(id)],
        );

        if (!rowSet.rows.length) {
            return notFound();
        }

        return jsonResponse(playerFromRow(rowSet.rows[0]));
    })
    .get('/players/record/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query(
            'SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, height::int as height, weight::int as weight FROM player WHERE id = $1',
            [parseId(id)],
        );

        if (!rowSet.rows.length) {
            return notFound();
        }

        const player = playerFromRow(rowSet.rows[0]);

        const attributesRowSet = connection.query(
            'SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2',
            [player.apiId, player.fifaApiId],
        );

        const attributes = attributesRowSet.rows.map(playerAttributesFromRow);
        return jsonResponse({ player, attributes });
    })
    .get('/teams/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query('SELECT * FROM team WHERE id = $1', [parseId(id)]);

        if (!rowSet.rows.length) {
            return notFound();
        }

        return jsonResponse(teamFromRow(rowSet.rows[0]));
    })
    .get('/teams/api-id/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query('SELECT * FROM team WHERE team_api_id = $1', [parseId(id)]);

        if (!rowSet.rows.length) {
            return notFound();
        }

        return jsonResponse(teamFromRow(rowSet.rows[0]));
    })
    .get('/teams/record/:id', ({ id }) => {
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
    })
    .get('/match/result-table', (request) => {
        const url = new URL(request.url);
        const season = url.searchParams.get('season');
        const leagueName = url.searchParams.get('leagueName');

        if (!season || !leagueName) {
            return textResponse(REQUIRED_RESULT_TABLE_QUERY_ERROR, 400);
        }

        const connection = getConnection();
        const rowSet = connection.query(
            'SELECT m.home_team_api_id, m.away_team_api_id, m.home_team_goal, m.away_team_goal FROM match m JOIN league l ON m.league_id = l.id WHERE m.season = $1 AND l.name = $2',
            [season, leagueName],
        );

        const matches = rowSet.rows.map((row) => ({
            homeTeamId: row.home_team_api_id,
            awayTeamId: row.away_team_api_id,
            homeTeamGoal: row.home_team_goal,
            awayTeamGoal: row.away_team_goal,
        }));

        const resultTable = buildResultTable(matches).map((row) => ({
            teamId: row.teamId,
            teamName: resolveTeamName(connection, row.teamId),
            points: row.points,
            wins: row.wins,
            draws: row.draws,
            losses: row.losses,
            goalsScored: row.goalsScored,
            goalsConceded: row.goalsConceded,
        }));

        return jsonResponse(resultTable);
    })
    .get('/match/:id', ({ id }) => {
        const connection = getConnection();
        const rowSet = connection.query(
            'SELECT id, country_id, league_id, season, stage, date::text as date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE id = $1',
            [parseId(id)],
        );

        if (!rowSet.rows.length) {
            return notFound();
        }

        const matchDto = matchDtoFromRow(rowSet.rows[0]);
        const homeTeamName = resolveTeamName(connection, matchDto.homeTeamApiId ?? 0);
        const awayTeamName = resolveTeamName(connection, matchDto.awayTeamApiId ?? 0);

        return jsonResponse(toMatchResource(matchDto, homeTeamName, awayTeamName));
    })
    .get('/match/team/:id', ({ id }) => {
        const connection = getConnection();
        const parsedId = parseId(id);
        const rowSet = connection.query(
            'SELECT id, country_id, league_id, season, stage, date::text as date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE home_team_api_id = $1 OR away_team_api_id = $1',
            [parsedId],
        );

        if (!rowSet.rows.length) {
            return notFound();
        }

        const resources = rowSet.rows.map((row) => {
            const matchDto = matchDtoFromRow(row);
            const homeTeamName = resolveTeamName(connection, matchDto.homeTeamApiId ?? 0);
            const awayTeamName = resolveTeamName(connection, matchDto.awayTeamApiId ?? 0);
            return toMatchResource(matchDto, homeTeamName, awayTeamName);
        });

        return jsonResponse(resources);
    });

addEventListener('fetch', (event) => {
    event.respondWith(router.fetch(event.request));
});

