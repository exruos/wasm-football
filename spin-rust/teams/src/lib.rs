mod db;

use anyhow::Result;
use axum::{
    Json, Router,
    extract::Path,
    http::StatusCode,
    response::{IntoResponse as AxumIntoResponse, Response as AxumResponse},
    routing::get,
};
use football_shared::domain::teams::TeamRecord;
use spin_sdk::http::Request;
#[cfg(feature = "spin-component")]
use spin_sdk::{http::IntoResponse, http_service};
use spin_sdk::pg::Connection;
use spin_sdk::variables;
use tower::util::ServiceExt;

use crate::db::{team_attributes_from_row, team_from_row};

pub fn register_routes(router: Router) -> Router {
    router
        .route("/teams/{id}", get(get_team_by_id))
        .route("/teams/api-id/{id}", get(get_team_by_api_id))
        .route("/teams/record/{id}", get(get_team_record_by_id))
}

#[cfg(feature = "spin-component")]
#[http_service]
async fn handle_request(req: Request) -> Result<impl IntoResponse> {
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

async fn get_team_by_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_team_by_id(id).await {
        Ok(Some(team)) => AxumIntoResponse::into_response((StatusCode::OK, Json(team))),
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn get_team_by_api_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_team_by_api_id(id).await {
        Ok(Some(team)) => AxumIntoResponse::into_response((StatusCode::OK, Json(team))),
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn get_team_record_by_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_team_record_by_id(id).await {
        Ok(Some(team_record)) => AxumIntoResponse::into_response((StatusCode::OK, Json(team_record))),
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn try_get_team_by_id(id: i32) -> Result<Option<football_shared::domain::teams::Team>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let mut query = conn.query("SELECT * FROM team WHERE id = $1", &[id.into()]).await?;
    let team = query.next().await.map(|row| team_from_row(&row)).transpose()?;
    query.result().await?;

    Ok(team)
}

async fn try_get_team_by_api_id(id: i32) -> Result<Option<football_shared::domain::teams::Team>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let mut query = conn
        .query("SELECT * FROM team WHERE team_api_id = $1", &[id.into()])
        .await?;
    let team = query.next().await.map(|row| team_from_row(&row)).transpose()?;
    query.result().await?;

    Ok(team)
}

async fn try_get_team_record_by_id(id: i32) -> Result<Option<TeamRecord>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let mut team_query = conn
        .query("SELECT * FROM team WHERE id = $1", &[id.into()])
        .await?;

    let team = match team_query.next().await {
        None => {
            team_query.result().await?;
            return Ok(None);
        }
        Some(row) => team_from_row(&row)?,
    };

    team_query.result().await?;

    let mut attributes_query = conn
        .query(
            "SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2",
            &[team.api_id.into(), team.fifa_api_id.into()],
        )
        .await?;

    let mut attributes = Vec::new();
    while let Some(row) = attributes_query.next().await {
        attributes.push(team_attributes_from_row(&row)?);
    }

    attributes_query.result().await?;

    Ok(Some(TeamRecord { team, attributes }))
}

fn internal_error_response(error: anyhow::Error) -> AxumResponse {
    AxumIntoResponse::into_response((StatusCode::INTERNAL_SERVER_ERROR, error.to_string()))
}
