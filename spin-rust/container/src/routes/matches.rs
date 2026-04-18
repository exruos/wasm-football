use super::{ApiError, AppState, ResultTableQuery};
use crate::routes::mappers::match_dto_from_row;
use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use football_shared::domain::matches::ResultTableRowResource;
use football_shared::services::result_table::{TableMatch, build_result_table};
use sqlx::Row;
use sqlx::postgres::PgPool;

pub(crate) async fn get_match_by_id(
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

pub(crate) async fn get_matches_by_team_id(
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

pub(crate) async fn get_result_table_by_season_and_league(
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
