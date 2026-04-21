mod db;

use anyhow::Result;
use axum::{
    Json, Router,
    extract::Path,
    http::StatusCode,
    response::{IntoResponse as AxumIntoResponse, Response as AxumResponse},
    routing::get,
};
use football_shared::domain::players::PlayerRecord;
use spin_sdk::http::Request;
#[cfg(feature = "spin-component")]
use spin_sdk::http_service;
use spin_sdk::pg::Connection;
use spin_sdk::variables;
use tower::util::ServiceExt;

use crate::db::{player_attributes_from_row, player_from_row};

pub fn register_routes(router: Router) -> Router {
    router
        .route("/players/{id}", get(get_player_by_id))
        .route("/players/record/{id}", get(get_player_record_by_id))
}

#[cfg(feature = "spin-component")]
#[http_service]
async fn handle_request(req: Request) -> Result<impl spin_sdk::http::IntoResponse> {
    let response = register_routes(Router::new())
        .oneshot(req)
        .await
        .unwrap_or_else(|err| match err {});

    Ok(response)
}

#[cfg(not(feature = "spin-component"))]
pub async fn handle_request(req: Request) -> Result<AxumResponse> {
    let response = register_routes(Router::new())
        .oneshot(req)
        .await
        .unwrap_or_else(|err| match err {});

    Ok(response)
}

async fn get_player_by_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_player_by_id(id).await {
        Ok(Some(player)) => AxumIntoResponse::into_response((StatusCode::OK, Json(player))),
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn get_player_record_by_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_player_record_by_id(id).await {
        Ok(Some(player_record)) => {
            AxumIntoResponse::into_response((StatusCode::OK, Json(player_record)))
        }
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn try_get_player_by_id(id: i32) -> Result<Option<football_shared::domain::players::Player>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let mut query_result = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()]).await?;

    let player = query_result.next().await.map(|row| player_from_row(&row)).transpose()?;

    query_result.result().await?;
    Ok(player)
}

async fn try_get_player_record_by_id(id: i32) -> Result<Option<PlayerRecord>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let mut player_query = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()]).await?;

    let player = match player_query.next().await {
        None => {
            player_query.result().await?;
            return Ok(None);
        }
        Some(row) => player_from_row(&row)?,
    };

    player_query.result().await?;

    let mut attributes_query = conn
        .query(
            "SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2",
            &[player.api_id.into(), player.fifa_api_id.into()],
        )
        .await?;

    let mut attributes = Vec::new();
    while let Some(row) = attributes_query.next().await {
        attributes.push(player_attributes_from_row(&row)?);
    }

    attributes_query.result().await?;

    Ok(Some(PlayerRecord { player, attributes }))
}

fn internal_error_response(error: anyhow::Error) -> AxumResponse {
    AxumIntoResponse::into_response((StatusCode::INTERNAL_SERVER_ERROR, error.to_string()))
}
