use super::{ApiError, AppState};
use crate::routes::mappers::{player_attributes_from_row, player_from_row};
use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use football_shared::domain::players::PlayerRecord;

pub(crate) async fn get_player_by_id(
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

pub(crate) async fn get_player_record_by_id(
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
