use super::{ApiError, AppState};
use crate::routes::mappers::{team_attributes_from_row, team_from_row};
use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use football_shared::domain::teams::TeamRecord;

pub(crate) async fn get_team_by_id(
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

pub(crate) async fn get_team_by_api_id(
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

pub(crate) async fn get_team_record_by_id(
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
