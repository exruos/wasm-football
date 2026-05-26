mod mappers;
mod matches;
mod players;
mod teams;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::extract::State;
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

#[derive(Serialize)]
struct ErrorResponse {
    status: u16,
    error: &'static str,
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
        .route("/ready", get(ready))
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
        .fallback(|| async {
            (
                StatusCode::NOT_FOUND,
                Json(ErrorResponse {
                    status: 404,
                    error: "Not Found",
                }),
            )
        })
        .with_state(state)
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

async fn ready(State(state): State<AppState>) -> impl IntoResponse {
    let res = sqlx::query_scalar::<_, i32>("SELECT 1")
        .fetch_one(&state.pool)
        .await;

    match res {
        Ok(1) => (StatusCode::OK, Json(HealthResponse { status: "ok" })).into_response(),
        _ => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(HealthResponse { status: "db-error" }),
        )
        .into_response(),
    }
}
