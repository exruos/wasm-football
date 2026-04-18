use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use chrono::{NaiveDate, NaiveDateTime};
use football_shared::domain::matches::{MatchDto, ResultTableRowResource};
use football_shared::domain::players::{Player, PlayerAttributes, PlayerRecord};
use football_shared::domain::teams::{Team, TeamAttributes, TeamRecord};
use football_shared::services::result_table::{TableMatch, build_result_table};
use serde::{Deserialize, Serialize};
use sqlx::Row;
use sqlx::postgres::{PgPool, PgRow};

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
}

#[derive(Deserialize)]
struct ResultTableQuery {
    season: Option<String>,
    #[serde(rename = "leagueName")]
    league_name: Option<String>,
}

struct ApiError(anyhow::Error);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (StatusCode::INTERNAL_SERVER_ERROR, self.0.to_string()).into_response()
    }
}

impl<E> From<E> for ApiError
where
    E: Into<anyhow::Error>,
{
    fn from(err: E) -> Self {
        Self(err.into())
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/players/{id}", get(get_player_by_id))
        .route("/players/record/{id}", get(get_player_record_by_id))
        .route("/teams/{id}", get(get_team_by_id))
        .route("/teams/api-id/{id}", get(get_team_by_api_id))
        .route("/teams/record/{id}", get(get_team_record_by_id))
        .route("/match/{id}", get(get_match_by_id))
        .route("/match/team/{id}", get(get_matches_by_team_id))
        .route(
            "/match/result-table",
            get(get_result_table_by_season_and_league),
        )
        .with_state(state)
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

async fn get_player_by_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query(
        "SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, \
         height::int as height, weight::int as weight FROM player WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await?;

    match row {
        None => Ok(StatusCode::NOT_FOUND.into_response()),
        Some(row) => Ok(Json(player_from_row(&row)?).into_response()),
    }
}

async fn get_player_record_by_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query(
        "SELECT id, player_api_id, player_fifa_api_id, player_name, birthday::text as birthday, \
         height::int as height, weight::int as weight FROM player WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await?;

    let player = match row {
        None => return Ok(StatusCode::NOT_FOUND.into_response()),
        Some(r) => player_from_row(&r)?,
    };

    let rows = sqlx::query(
        "SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2",
    )
    .bind(player.api_id)
    .bind(player.fifa_api_id)
    .fetch_all(&state.pool)
    .await?;

    let mut attributes = Vec::with_capacity(rows.len());
    for row in rows {
        attributes.push(player_attributes_from_row(&row)?);
    }

    Ok(Json(PlayerRecord { player, attributes }).into_response())
}

async fn get_team_by_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query("SELECT * FROM team WHERE id = $1")
        .bind(id)
        .fetch_optional(&state.pool)
        .await?;

    match row {
        None => Ok(StatusCode::NOT_FOUND.into_response()),
        Some(row) => Ok(Json(team_from_row(&row)?).into_response()),
    }
}

async fn get_team_by_api_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query("SELECT * FROM team WHERE team_api_id = $1")
        .bind(id)
        .fetch_optional(&state.pool)
        .await?;

    match row {
        None => Ok(StatusCode::NOT_FOUND.into_response()),
        Some(row) => Ok(Json(team_from_row(&row)?).into_response()),
    }
}

async fn get_team_record_by_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query("SELECT * FROM team WHERE team_api_id = $1")
        .bind(id)
        .fetch_optional(&state.pool)
        .await?;

    let team = match row {
        None => return Ok(StatusCode::NOT_FOUND.into_response()),
        Some(r) => team_from_row(&r)?,
    };

    let rows = sqlx::query(
        "SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2",
    )
    .bind(team.team_api_id)
    .bind(team.team_fifa_api_id)
    .fetch_all(&state.pool)
    .await?;

    let mut attributes = Vec::with_capacity(rows.len());
    for row in rows {
        attributes.push(team_attributes_from_row(&row)?);
    }

    Ok(Json(TeamRecord { team, attributes }).into_response())
}

async fn get_match_by_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let row = sqlx::query(
        "SELECT id, country_id, league_id, season, stage, date::text as date, match_api_id, \
         home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, \
         home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, \
         home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, \
         away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, \
         away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, \
         away_player_x11 FROM match WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await?;

    match row {
        None => Ok(StatusCode::NOT_FOUND.into_response()),
        Some(row) => {
            let match_dto = match_dto_from_row(&row)?;
            let home_team_name =
                resolve_team_name(&state.pool, match_dto.home_team_api_id.unwrap_or(0)).await?;
            let away_team_name =
                resolve_team_name(&state.pool, match_dto.away_team_api_id.unwrap_or(0)).await?;
            let resource = match_dto.to_match_resource(home_team_name, away_team_name);
            Ok(Json(resource).into_response())
        }
    }
}

async fn get_matches_by_team_id(
    State(state): State<AppState>,
    Path(id): Path<i32>,
) -> Result<Response, ApiError> {
    let rows = sqlx::query(
        "SELECT id, country_id, league_id, season, stage, date::text as date, match_api_id, \
         home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, \
         home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, \
         home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, \
         away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, \
         away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, \
         away_player_x11 FROM match WHERE home_team_api_id = $1 OR away_team_api_id = $1",
    )
    .bind(id)
    .fetch_all(&state.pool)
    .await?;

    if rows.is_empty() {
        return Ok(StatusCode::NOT_FOUND.into_response());
    }

    let mut matches = Vec::with_capacity(rows.len());
    for row in rows {
        let match_dto = match_dto_from_row(&row)?;
        let home_team_name =
            resolve_team_name(&state.pool, match_dto.home_team_api_id.unwrap_or(0)).await?;
        let away_team_name =
            resolve_team_name(&state.pool, match_dto.away_team_api_id.unwrap_or(0)).await?;
        matches.push(match_dto.to_match_resource(home_team_name, away_team_name));
    }

    Ok(Json(matches).into_response())
}

async fn get_result_table_by_season_and_league(
    State(state): State<AppState>,
    Query(query): Query<ResultTableQuery>,
) -> Result<Response, ApiError> {
    let season = match query.season {
        Some(v) if !v.is_empty() => v,
        _ => {
            return Ok((
                StatusCode::BAD_REQUEST,
                "Missing required query parameters: season and leagueName",
            )
                .into_response());
        }
    };

    let league_name = match query.league_name {
        Some(v) if !v.is_empty() => v,
        _ => {
            return Ok((
                StatusCode::BAD_REQUEST,
                "Missing required query parameters: season and leagueName",
            )
                .into_response());
        }
    };

    let rows = sqlx::query(
        "SELECT m.home_team_api_id, m.away_team_api_id, m.home_team_goal, m.away_team_goal \
         FROM match m JOIN league l ON m.league_id = l.id \
         WHERE m.season = $1 AND l.name = $2",
    )
    .bind(&season)
    .bind(&league_name)
    .fetch_all(&state.pool)
    .await?;

    let matches: Vec<TableMatch> = rows
        .iter()
        .map(|row| TableMatch {
            home_team_id: row.get("home_team_api_id"),
            away_team_id: row.get("away_team_api_id"),
            home_team_goal: row.get("home_team_goal"),
            away_team_goal: row.get("away_team_goal"),
        })
        .collect();

    let table = build_result_table(&matches);
    let mut resources: Vec<ResultTableRowResource> = Vec::with_capacity(table.len());

    for row in table {
        let team_name = resolve_team_name(&state.pool, row.team_id).await?;
        resources.push(row.to_resource(team_name));
    }

    Ok(Json(resources).into_response())
}

async fn resolve_team_name(pool: &PgPool, team_id: i32) -> Result<String, ApiError> {
    let row = sqlx::query("SELECT team_long_name FROM team WHERE team_api_id = $1")
        .bind(team_id)
        .fetch_optional(pool)
        .await?;

    match row {
        None => Ok("Unknown".to_string()),
        Some(r) => Ok(r
            .try_get::<String, _>("team_long_name")
            .unwrap_or_else(|_| "Unknown".to_string())),
    }
}

fn parse_date(date_text: &str) -> Option<NaiveDate> {
    NaiveDateTime::parse_from_str(date_text, "%Y-%m-%d %H:%M:%S")
        .ok()
        .map(|dt| dt.date())
}

fn player_from_row(row: &PgRow) -> anyhow::Result<Player> {
    Ok(Player {
        id: row.try_get("id")?,
        api_id: row.try_get("player_api_id")?,
        fifa_api_id: row.try_get("player_fifa_api_id")?,
        name: row.try_get("player_name")?,
        birthday: row
            .try_get::<String, _>("birthday")
            .unwrap_or_default()
            .split(' ')
            .next()
            .unwrap_or_default()
            .to_string(),
        height: row.try_get("height")?,
        weight: row.try_get("weight")?,
    })
}

fn player_attributes_from_row(row: &PgRow) -> anyhow::Result<PlayerAttributes> {
    let date_text: String = row.try_get("date")?;
    let date = parse_date(&date_text)
        .map(|d| d.to_string())
        .unwrap_or_default();

    Ok(PlayerAttributes {
        date,
        overall_rating: row.try_get("overall_rating")?,
        potential: row.try_get("potential")?,
        preferred_foot: row.try_get("preferred_foot")?,
        attacking_work_rate: row.try_get("attacking_work_rate")?,
        defensive_work_rate: row.try_get("defensive_work_rate")?,
        crossing: row.try_get("crossing")?,
        finishing: row.try_get("finishing")?,
        heading_accuracy: row.try_get("heading_accuracy")?,
        short_passing: row.try_get("short_passing")?,
        volleys: row.try_get("volleys")?,
        dribbling: row.try_get("dribbling")?,
        curve: row.try_get("curve")?,
        free_kick_accuracy: row.try_get("free_kick_accuracy")?,
        long_passing: row.try_get("long_passing")?,
        ball_control: row.try_get("ball_control")?,
        acceleration: row.try_get("acceleration")?,
        sprint_speed: row.try_get("sprint_speed")?,
        agility: row.try_get("agility")?,
        reactions: row.try_get("reactions")?,
        balance: row.try_get("balance")?,
        shot_power: row.try_get("shot_power")?,
        jumping: row.try_get("jumping")?,
        stamina: row.try_get("stamina")?,
        strength: row.try_get("strength")?,
        long_shots: row.try_get("long_shots")?,
        aggression: row.try_get("aggression")?,
        interceptions: row.try_get("interceptions")?,
        positioning: row.try_get("positioning")?,
        vision: row.try_get("vision")?,
        penalties: row.try_get("penalties")?,
        marking: row.try_get("marking")?,
        standing_tackle: row.try_get("standing_tackle")?,
        sliding_tackle: row.try_get("sliding_tackle")?,
        gk_diving: row.try_get("gk_diving")?,
        gk_handling: row.try_get("gk_handling")?,
        gk_kicking: row.try_get("gk_kicking")?,
        gk_positioning: row.try_get("gk_positioning")?,
        gk_reflexes: row.try_get("gk_reflexes")?,
    })
}

fn team_from_row(row: &PgRow) -> anyhow::Result<Team> {
    Ok(Team {
        id: row.try_get("id")?,
        team_api_id: row.try_get("team_api_id")?,
        team_fifa_api_id: row.try_get("team_fifa_api_id")?,
        team_long_name: row.try_get("team_long_name")?,
        team_short_name: row.try_get("team_short_name")?,
    })
}

fn team_attributes_from_row(row: &PgRow) -> anyhow::Result<TeamAttributes> {
    let date_text: String = row.try_get("date")?;
    let date = parse_date(&date_text)
        .map(|d| d.to_string())
        .unwrap_or_default();

    Ok(TeamAttributes {
        id: row.try_get("id")?,
        team_fifa_api_id: row.try_get("team_fifa_api_id")?,
        team_api_id: row.try_get("team_api_id")?,
        date,
        build_up_play_speed: row.try_get("buildupplayspeed")?,
        build_up_play_speed_class: row.try_get("buildupplayspeedclass")?,
        build_up_play_dribbling: row.try_get("buildupplaydribbling")?,
        build_up_play_dribbling_class: row.try_get("buildupplaydribblingclass")?,
        build_up_play_passing: row.try_get("buildupplaypassing")?,
        build_up_play_passing_class: row.try_get("buildupplaypassingclass")?,
        build_up_play_positioning_class: row.try_get("buildupplaypositioningclass")?,
        chance_creation_passing: row.try_get("chancecreationpassing")?,
        chance_creation_passing_class: row.try_get("chancecreationpassingclass")?,
        chance_creation_crossing: row.try_get("chancecreationcrossing")?,
        chance_creation_crossing_class: row.try_get("chancecreationcrossingclass")?,
        chance_creation_shooting: row.try_get("chancecreationshooting")?,
        chance_creation_shooting_class: row.try_get("chancecreationshootingclass")?,
        chance_creation_positioning_class: row.try_get("chancecreationpositioningclass")?,
        defence_pressure: row.try_get("defencepressure")?,
        defence_pressure_class: row.try_get("defencepressureclass")?,
        defence_aggression: row.try_get("defenceaggression")?,
        defence_aggression_class: row.try_get("defenceaggressionclass")?,
        defence_team_width: row.try_get("defenceteamwidth")?,
        defence_team_width_class: row.try_get("defenceteamwidthclass")?,
        defence_defender_line_class: row.try_get("defencedefenderlineclass")?,
    })
}

fn match_dto_from_row(row: &PgRow) -> anyhow::Result<MatchDto> {
    let date_text = row.try_get::<String, _>("date").unwrap_or_default();

    Ok(MatchDto {
        id: row.try_get("id")?,
        country_id: row.try_get("country_id")?,
        league_id: row.try_get("league_id")?,
        season: row.try_get("season")?,
        stage: row.try_get("stage")?,
        date: parse_date(&date_text),
        match_api_id: row.try_get("match_api_id")?,
        home_team_api_id: row.try_get("home_team_api_id")?,
        away_team_api_id: row.try_get("away_team_api_id")?,
        home_team_goal: row.try_get("home_team_goal")?,
        away_team_goal: row.try_get("away_team_goal")?,
        home_player_x1: row.try_get("home_player_x1")?,
        home_player_x2: row.try_get("home_player_x2")?,
        home_player_x3: row.try_get("home_player_x3")?,
        home_player_x4: row.try_get("home_player_x4")?,
        home_player_x5: row.try_get("home_player_x5")?,
        home_player_x6: row.try_get("home_player_x6")?,
        home_player_x7: row.try_get("home_player_x7")?,
        home_player_x8: row.try_get("home_player_x8")?,
        home_player_x9: row.try_get("home_player_x9")?,
        home_player_x10: row.try_get("home_player_x10")?,
        home_player_x11: row.try_get("home_player_x11")?,
        away_player_x1: row.try_get("away_player_x1")?,
        away_player_x2: row.try_get("away_player_x2")?,
        away_player_x3: row.try_get("away_player_x3")?,
        away_player_x4: row.try_get("away_player_x4")?,
        away_player_x5: row.try_get("away_player_x5")?,
        away_player_x6: row.try_get("away_player_x6")?,
        away_player_x7: row.try_get("away_player_x7")?,
        away_player_x8: row.try_get("away_player_x8")?,
        away_player_x9: row.try_get("away_player_x9")?,
        away_player_x10: row.try_get("away_player_x10")?,
        away_player_x11: row.try_get("away_player_x11")?,
    })
}
