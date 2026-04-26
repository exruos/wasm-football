import json
import re
from urllib.parse import parse_qs, urlsplit

from spin_sdk.http import Handler, Request, Response
from spin_sdk import variables
from spin_sdk.wit.imports import spin_postgres_postgres_4_2_0 as pg

DB_URL_DEFAULT = "postgres://postgres:postgres@localhost:5438/postgres"
REQUIRED_RESULT_TABLE_QUERY_ERROR = "Missing required query parameters: season and leagueName"

SELECT_MATCH_RESOURCE_FIELDS = (
    "SELECT m.id, m.country_id, m.league_id, m.season, m.stage, "
    "m.date::text as date, m.match_api_id, m.home_team_api_id, m.away_team_api_id, "
    "m.home_team_goal, m.away_team_goal, "
    "m.home_player_x1, m.home_player_x2, m.home_player_x3, m.home_player_x4, m.home_player_x5, "
    "m.home_player_x6, m.home_player_x7, m.home_player_x8, m.home_player_x9, m.home_player_x10, m.home_player_x11, "
    "m.away_player_x1, m.away_player_x2, m.away_player_x3, m.away_player_x4, m.away_player_x5, "
    "m.away_player_x6, m.away_player_x7, m.away_player_x8, m.away_player_x9, m.away_player_x10, m.away_player_x11 "
    "FROM match m"
)


def json_response(payload: object, status: int = 200) -> Response:
    return Response(
        status,
        {"content-type": "application/json"},
        json.dumps(payload).encode("utf-8"),
    )


def text_response(message: str, status: int) -> Response:
    return Response(status, {"content-type": "text/plain"}, message.encode("utf-8"))


def not_found() -> Response:
    return Response(404, {}, None)


def parse_id(raw_id: str | None) -> int:
    try:
        return int(raw_id or "")
    except ValueError:
        return 0


def format_date(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.split(" ")[0] if value else ""


def db_value_to_python(value: object) -> object:
    if isinstance(value, pg.DbValue_DbNull):
        return None
    if hasattr(value, "value"):
        return value.value
    return value


def row_to_dict(columns: list[pg.Column], row: list[object]) -> dict[str, object]:
    return {column.name: db_value_to_python(cell) for column, cell in zip(columns, row)}


def to_param(value: object) -> object:
    if value is None:
        return pg.ParameterValue_DbNull()
    if isinstance(value, bool):
        return pg.ParameterValue_Boolean(value)
    if isinstance(value, int):
        return pg.ParameterValue_Int32(value)
    return pg.ParameterValue_Str(str(value))


def query_rows(connection: pg.Connection, sql: str, params: list[object]) -> list[dict[str, object]]:
    row_set = connection.query(sql, [to_param(param) for param in params])
    return [row_to_dict(row_set.columns, row) for row in row_set.rows]


def resolve_team_name(connection: pg.Connection, team_id: int, cache: dict[int, str] | None = None) -> str:
    if cache is not None and team_id in cache:
        return cache[team_id]

    rows = query_rows(connection, "SELECT team_long_name FROM team WHERE team_api_id = $1", [team_id])
    team_name = str(rows[0].get("team_long_name") or "Unknown") if rows else "Unknown"

    if cache is not None:
        cache[team_id] = team_name

    return team_name


def player_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row.get("id"),
        "apiId": row.get("player_api_id"),
        "fifaApiId": row.get("player_fifa_api_id"),
        "name": row.get("player_name"),
        "birthday": format_date(row.get("birthday")),
        "height": row.get("height"),
        "weight": row.get("weight"),
    }


def player_attributes_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "date": format_date(row.get("date")),
        "overallRating": row.get("overall_rating"),
        "potential": row.get("potential"),
        "preferredFoot": row.get("preferred_foot"),
        "attackingWorkRate": row.get("attacking_work_rate"),
        "defensiveWorkRate": row.get("defensive_work_rate"),
        "crossing": row.get("crossing"),
        "finishing": row.get("finishing"),
        "headingAccuracy": row.get("heading_accuracy"),
        "shortPassing": row.get("short_passing"),
        "volleys": row.get("volleys"),
        "dribbling": row.get("dribbling"),
        "curve": row.get("curve"),
        "freeKickAccuracy": row.get("free_kick_accuracy"),
        "longPassing": row.get("long_passing"),
        "ballControl": row.get("ball_control"),
        "acceleration": row.get("acceleration"),
        "sprintSpeed": row.get("sprint_speed"),
        "agility": row.get("agility"),
        "reactions": row.get("reactions"),
        "balance": row.get("balance"),
        "shotPower": row.get("shot_power"),
        "jumping": row.get("jumping"),
        "stamina": row.get("stamina"),
        "strength": row.get("strength"),
        "longShots": row.get("long_shots"),
        "aggression": row.get("aggression"),
        "interceptions": row.get("interceptions"),
        "positioning": row.get("positioning"),
        "vision": row.get("vision"),
        "penalties": row.get("penalties"),
        "marking": row.get("marking"),
        "standingTackle": row.get("standing_tackle"),
        "slidingTackle": row.get("sliding_tackle"),
        "gkDiving": row.get("gk_diving"),
        "gkHandling": row.get("gk_handling"),
        "gkKicking": row.get("gk_kicking"),
        "gkPositioning": row.get("gk_positioning"),
        "gkReflexes": row.get("gk_reflexes"),
    }


def team_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row.get("id"),
        "teamApiId": row.get("team_api_id"),
        "teamFifaApiId": row.get("team_fifa_api_id"),
        "teamLongName": row.get("team_long_name"),
        "teamShortName": row.get("team_short_name"),
    }


def team_attributes_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row.get("id"),
        "teamFifaApiId": row.get("team_fifa_api_id"),
        "teamApiId": row.get("team_api_id"),
        "date": format_date(row.get("date")),
        "buildUpPlaySpeed": row.get("buildupplayspeed"),
        "buildUpPlaySpeedClass": row.get("buildupplayspeedclass"),
        "buildUpPlayDribbling": row.get("buildupplaydribbling"),
        "buildUpPlayDribblingClass": row.get("buildupplaydribblingclass"),
        "buildUpPlayPassing": row.get("buildupplaypassing"),
        "buildUpPlayPassingClass": row.get("buildupplaypassingclass"),
        "buildUpPlayPositioningClass": row.get("buildupplaypositioningclass"),
        "chanceCreationPassing": row.get("chancecreationpassing"),
        "chanceCreationPassingClass": row.get("chancecreationpassingclass"),
        "chanceCreationCrossing": row.get("chancecreationcrossing"),
        "chanceCreationCrossingClass": row.get("chancecreationcrossingclass"),
        "chanceCreationShooting": row.get("chancecreationshooting"),
        "chanceCreationShootingClass": row.get("chancecreationshootingclass"),
        "chanceCreationPositioningClass": row.get("chancecreationpositioningclass"),
        "defencePressure": row.get("defencepressure"),
        "defencePressureClass": row.get("defencepressureclass"),
        "defenceAggression": row.get("defenceaggression"),
        "defenceAggressionClass": row.get("defenceaggressionclass"),
        "defenceTeamWidth": row.get("defenceteamwidth"),
        "defenceTeamWidthClass": row.get("defenceteamwidthclass"),
        "defenceDefenderLineClass": row.get("defencedefenderlineclass"),
    }


def match_dto_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row.get("id"),
        "countryId": row.get("country_id"),
        "leagueId": row.get("league_id"),
        "season": row.get("season"),
        "stage": row.get("stage"),
        "date": format_date(row.get("date")),
        "matchApiId": row.get("match_api_id"),
        "homeTeamApiId": row.get("home_team_api_id"),
        "awayTeamApiId": row.get("away_team_api_id"),
        "homeTeamGoal": row.get("home_team_goal"),
        "awayTeamGoal": row.get("away_team_goal"),
        "homePlayerX1": row.get("home_player_x1"),
        "homePlayerX2": row.get("home_player_x2"),
        "homePlayerX3": row.get("home_player_x3"),
        "homePlayerX4": row.get("home_player_x4"),
        "homePlayerX5": row.get("home_player_x5"),
        "homePlayerX6": row.get("home_player_x6"),
        "homePlayerX7": row.get("home_player_x7"),
        "homePlayerX8": row.get("home_player_x8"),
        "homePlayerX9": row.get("home_player_x9"),
        "homePlayerX10": row.get("home_player_x10"),
        "homePlayerX11": row.get("home_player_x11"),
        "awayPlayerX1": row.get("away_player_x1"),
        "awayPlayerX2": row.get("away_player_x2"),
        "awayPlayerX3": row.get("away_player_x3"),
        "awayPlayerX4": row.get("away_player_x4"),
        "awayPlayerX5": row.get("away_player_x5"),
        "awayPlayerX6": row.get("away_player_x6"),
        "awayPlayerX7": row.get("away_player_x7"),
        "awayPlayerX8": row.get("away_player_x8"),
        "awayPlayerX9": row.get("away_player_x9"),
        "awayPlayerX10": row.get("away_player_x10"),
        "awayPlayerX11": row.get("away_player_x11"),
    }


def to_match_resource(match_dto: dict[str, object], home_team_name: str, away_team_name: str) -> dict[str, object]:
    return {
        "matchId": match_dto.get("id"),
        "countryId": match_dto.get("countryId") or 0,
        "leagueId": match_dto.get("leagueId") or 0,
        "season": match_dto.get("season") or "",
        "stage": match_dto.get("stage") or 0,
        "date": match_dto.get("date") or "",
        "matchApiId": match_dto.get("matchApiId") or 0,
        "homeTeamId": match_dto.get("homeTeamApiId") or 0,
        "awayTeamId": match_dto.get("awayTeamApiId") or 0,
        "homeTeamName": home_team_name,
        "awayTeamName": away_team_name,
        "homeTeamGoal": match_dto.get("homeTeamGoal"),
        "awayTeamGoal": match_dto.get("awayTeamGoal"),
        "homePlayerLineup": {
            "player1": match_dto.get("homePlayerX1"),
            "player2": match_dto.get("homePlayerX2"),
            "player3": match_dto.get("homePlayerX3"),
            "player4": match_dto.get("homePlayerX4"),
            "player5": match_dto.get("homePlayerX5"),
            "player6": match_dto.get("homePlayerX6"),
            "player7": match_dto.get("homePlayerX7"),
            "player8": match_dto.get("homePlayerX8"),
            "player9": match_dto.get("homePlayerX9"),
            "player10": match_dto.get("homePlayerX10"),
            "player11": match_dto.get("homePlayerX11"),
        },
        "awayPlayerLineup": {
            "player1": match_dto.get("awayPlayerX1"),
            "player2": match_dto.get("awayPlayerX2"),
            "player3": match_dto.get("awayPlayerX3"),
            "player4": match_dto.get("awayPlayerX4"),
            "player5": match_dto.get("awayPlayerX5"),
            "player6": match_dto.get("awayPlayerX6"),
            "player7": match_dto.get("awayPlayerX7"),
            "player8": match_dto.get("awayPlayerX8"),
            "player9": match_dto.get("awayPlayerX9"),
            "player10": match_dto.get("awayPlayerX10"),
            "player11": match_dto.get("awayPlayerX11"),
        },
    }


def build_result_table(matches: list[dict[str, object]]) -> list[dict[str, object]]:
    team_stats: dict[int, dict[str, int]] = {}

    def record_match(team_id: int, scored: int, conceded: int) -> None:
        current = team_stats.get(
            team_id,
            {
                "points": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goalsScored": 0,
                "goalsConceded": 0,
            },
        )

        current["goalsScored"] += scored
        current["goalsConceded"] += conceded

        if scored > conceded:
            current["wins"] += 1
            current["points"] += 3
        elif scored == conceded:
            current["draws"] += 1
            current["points"] += 1
        else:
            current["losses"] += 1

        team_stats[team_id] = current

    for match in matches:
        home_team_goal = match.get("homeTeamGoal")
        away_team_goal = match.get("awayTeamGoal")
        if home_team_goal is None or away_team_goal is None:
            continue

        home_team_id = int(match.get("homeTeamId") or 0)
        away_team_id = int(match.get("awayTeamId") or 0)
        record_match(home_team_id, int(home_team_goal), int(away_team_goal))
        record_match(away_team_id, int(away_team_goal), int(home_team_goal))

    table = [
        {
            "teamId": team_id,
            "points": stats["points"],
            "wins": stats["wins"],
            "draws": stats["draws"],
            "losses": stats["losses"],
            "goalsScored": stats["goalsScored"],
            "goalsConceded": stats["goalsConceded"],
        }
        for team_id, stats in team_stats.items()
    ]

    return sorted(
        table,
        key=lambda row: (
            -int(row["points"]),
            -(int(row["goalsScored"]) - int(row["goalsConceded"])),
            -int(row["goalsScored"]),
        ),
    )


def extract_id(path: str, pattern: str) -> str | None:
    match = re.fullmatch(pattern, path)
    if match is None:
        return None
    return match.group(1)


async def get_connection() -> pg.Connection:
    try:
        db_url = await variables.get("db_url")
    except Exception:
        db_url = DB_URL_DEFAULT
    return pg.Connection.open(db_url or DB_URL_DEFAULT)


async def handle_players(connection: pg.Connection, path: str) -> Response | None:
    record_id = extract_id(path, r"/players/record/([^/]+)")
    if record_id is not None:
        rows = query_rows(
            connection,
            "SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, height::int as height, weight::int as weight FROM player WHERE id = $1",
            [parse_id(record_id)],
        )
        if not rows:
            return not_found()

        player = player_from_row(rows[0])
        attributes_rows = query_rows(
            connection,
            "SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2",
            [player.get("apiId") or 0, player.get("fifaApiId") or 0],
        )
        attributes = [player_attributes_from_row(row) for row in attributes_rows]
        return json_response({"player": player, "attributes": attributes})

    player_id = extract_id(path, r"/players/([^/]+)")
    if player_id is not None:
        rows = query_rows(
            connection,
            "SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, height::int as height, weight::int as weight FROM player WHERE id = $1",
            [parse_id(player_id)],
        )
        if not rows:
            return not_found()
        return json_response(player_from_row(rows[0]))

    return None


async def handle_teams(connection: pg.Connection, path: str) -> Response | None:
    api_id = extract_id(path, r"/teams/api-id/([^/]+)")
    if api_id is not None:
        rows = query_rows(connection, "SELECT * FROM team WHERE team_api_id = $1", [parse_id(api_id)])
        if not rows:
            return not_found()
        return json_response(team_from_row(rows[0]))

    record_id = extract_id(path, r"/teams/record/([^/]+)")
    if record_id is not None:
        team_rows = query_rows(connection, "SELECT * FROM team WHERE team_api_id = $1", [parse_id(record_id)])
        if not team_rows:
            return not_found()

        team = team_from_row(team_rows[0])
        attributes_rows = query_rows(
            connection,
            "SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2",
            [team.get("teamApiId") or 0, team.get("teamFifaApiId") or 0],
        )
        attributes = [team_attributes_from_row(row) for row in attributes_rows]
        return json_response({"team": team, "attributes": attributes})

    team_id = extract_id(path, r"/teams/([^/]+)")
    if team_id is not None:
        rows = query_rows(connection, "SELECT * FROM team WHERE id = $1", [parse_id(team_id)])
        if not rows:
            return not_found()
        return json_response(team_from_row(rows[0]))

    return None


async def handle_match(connection: pg.Connection, path: str, query: dict[str, list[str]]) -> Response | None:
    if path == "/match/result-table":
        season = query.get("season", [None])[0]
        league_name = query.get("leagueName", [None])[0]
        if not season or not league_name:
            return text_response(REQUIRED_RESULT_TABLE_QUERY_ERROR, 400)

        rows = query_rows(
            connection,
            "SELECT m.home_team_api_id, m.away_team_api_id, m.home_team_goal, m.away_team_goal "
            "FROM match m "
            "JOIN league l ON m.league_id = l.id "
            "WHERE m.season = $1 AND l.name = $2",
            [season, league_name],
        )

        matches = [
            {
                "homeTeamId": row.get("home_team_api_id"),
                "awayTeamId": row.get("away_team_api_id"),
                "homeTeamGoal": row.get("home_team_goal"),
                "awayTeamGoal": row.get("away_team_goal"),
            }
            for row in rows
        ]

        team_names: dict[int, str] = {}

        result_table = build_result_table(matches)
        return json_response(
            [
                {
                    "teamId": row["teamId"],
                    "teamName": resolve_team_name(connection, int(row["teamId"]), team_names),
                    "points": row["points"],
                    "wins": row["wins"],
                    "draws": row["draws"],
                    "losses": row["losses"],
                    "goalsScored": row["goalsScored"],
                    "goalsConceded": row["goalsConceded"],
                }
                for row in result_table
            ]
        )

    team_id = extract_id(path, r"/match/team/([^/]+)")
    if team_id is not None:
        rows = query_rows(
            connection,
            f"{SELECT_MATCH_RESOURCE_FIELDS} WHERE m.home_team_api_id = $1 OR m.away_team_api_id = $1",
            [parse_id(team_id)],
        )
        if not rows:
            return not_found()

        team_names: dict[int, str] = {}

        resources = [
            to_match_resource(
                match_dto_from_row(row),
                resolve_team_name(connection, int(row.get("home_team_api_id") or 0), team_names),
                resolve_team_name(connection, int(row.get("away_team_api_id") or 0), team_names),
            )
            for row in rows
        ]
        return json_response(resources)

    match_id = extract_id(path, r"/match/([^/]+)")
    if match_id is not None:
        rows = query_rows(
            connection,
            f"{SELECT_MATCH_RESOURCE_FIELDS} WHERE m.id = $1",
            [parse_id(match_id)],
        )
        if not rows:
            return not_found()

        match_row = rows[0]
        return json_response(
            to_match_resource(
                match_dto_from_row(match_row),
                resolve_team_name(connection, int(match_row.get("home_team_api_id") or 0)),
                resolve_team_name(connection, int(match_row.get("away_team_api_id") or 0)),
            )
        )

    return None


class HttpHandler(Handler):
    async def handle_request(self, request: Request) -> Response:
        if request.method != "GET":
            return not_found()

        parsed_uri = urlsplit(request.uri)
        path = parsed_uri.path or "/"
        query = parse_qs(parsed_uri.query)

        if path == "/health":
            return json_response({"status": "ok"})

        connection = await get_connection()

        players_response = await handle_players(connection, path)
        if players_response is not None:
            return players_response

        teams_response = await handle_teams(connection, path)
        if teams_response is not None:
            return teams_response

        match_response = await handle_match(connection, path, query)
        if match_response is not None:
            return match_response

        return not_found()
