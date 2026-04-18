mod mappers;
mod matches;
mod players;
mod teams;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use sqlx::postgres::PgPool;

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
        .route("/players/{id}", get(players::get_player_by_id))
        .route(
            "/players/record/{id}",
            get(players::get_player_record_by_id),
        )
        .route("/teams/{id}", get(teams::get_team_by_id))
        .route("/teams/api-id/{id}", get(teams::get_team_by_api_id))
        .route("/teams/record/{id}", get(teams::get_team_record_by_id))
        .route("/match/{id}", get(matches::get_match_by_id))
        .route("/match/team/{id}", get(matches::get_matches_by_team_id))
        .route(
            "/match/result-table",
            get(matches::get_result_table_by_season_and_league),
        )
        .with_state(state)
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}
