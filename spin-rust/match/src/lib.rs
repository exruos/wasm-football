mod db;

use anyhow::Result;
use axum::{
    Json, Router,
    extract::{Path, Query},
    http::StatusCode,
    response::{IntoResponse as AxumIntoResponse, Response as AxumResponse},
    routing::get,
};
use football_shared::domain::matches::{MatchResource, ResultTableRowResource};
use football_shared::services::result_table::{TableMatch, build_result_table};
use serde::Deserialize;
use serde_json::Value;
use spin_sdk::http::{
    EmptyBody, Method, Request, Response,
    body::IncomingBodyExt,
};
#[cfg(feature = "spin-component")]
use spin_sdk::http_service;
use spin_sdk::pg::Connection;
use spin_sdk::variables;
use tower::util::ServiceExt;

use crate::db::match_dto_from_row;

pub fn register_routes(router: Router) -> Router {
    router
        .route("/match/{id}", get(get_match_by_id))
        .route("/match/team/{id}", get(get_matches_by_team_id))
        .route(
            "/match/result-table",
            get(get_result_table_by_season_and_league),
        )
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

async fn get_match_by_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_match_by_id(id).await {
        Ok(Some(resource)) => AxumIntoResponse::into_response((StatusCode::OK, Json(resource))),
        Ok(None) => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Err(error) => internal_error_response(error),
    }
}

async fn get_matches_by_team_id(Path(id): Path<i32>) -> AxumResponse {
    match try_get_matches_by_team_id(id).await {
        Ok(resources) if resources.is_empty() => AxumIntoResponse::into_response(StatusCode::NOT_FOUND),
        Ok(resources) => AxumIntoResponse::into_response((StatusCode::OK, Json(resources))),
        Err(error) => internal_error_response(error),
    }
}

#[derive(Deserialize)]
struct ResultTableQuery {
    season: String,
    #[serde(rename = "leagueName")]
    league_name: String,
}

async fn get_result_table_by_season_and_league(
    Query(query): Query<ResultTableQuery>,
) -> AxumResponse {

    match try_get_result_table_by_season_and_league(&query.season, &query.league_name).await {
        Ok(rows) => (StatusCode::OK, Json(rows)).into_response(),
        Err(error) => internal_error_response(error),
    }
}

async fn try_get_match_by_id(id: i32) -> Result<Option<MatchResource>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let sql = "SELECT id, country_id, league_id, season, stage, date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE id = $1";

    let mut query = conn.query(sql, &[id.into()]).await?;

    let resource = match query.next().await {
        None => None,
        Some(row) => {
            let match_dto = match_dto_from_row(&row)?;
            let home_team_name = resolve_team_name(match_dto.home_team_api_id.unwrap_or(0)).await?;
            let away_team_name = resolve_team_name(match_dto.away_team_api_id.unwrap_or(0)).await?;
            Some(match_dto.to_match_resource(home_team_name, away_team_name))
        }
    };

    query.result().await?;
    Ok(resource)
}

async fn try_get_matches_by_team_id(id: i32) -> Result<Vec<MatchResource>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let sql = "SELECT id, country_id, league_id, season, stage, date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE home_team_api_id = $1 OR away_team_api_id = $1";

    let mut query = conn.query(sql, &[id.into()]).await?;

    let mut matches = Vec::new();
    while let Some(row) = query.next().await {
        let match_dto = match_dto_from_row(&row)?;
        let home_team_name = resolve_team_name(match_dto.home_team_api_id.unwrap_or(0)).await?;
        let away_team_name = resolve_team_name(match_dto.away_team_api_id.unwrap_or(0)).await?;
        matches.push(match_dto.to_match_resource(home_team_name, away_team_name));
    }

    query.result().await?;
    Ok(matches)
}

async fn try_get_result_table_by_season_and_league(
    season: &str,
    league_name: &str,
) -> Result<Vec<ResultTableRowResource>> {
    let address = variables::get("db_url").await?;
    let conn = Connection::open(&address).await?;

    let sql = "
        SELECT
            m.id,
            m.home_team_api_id,
            m.away_team_api_id,
            m.home_team_goal,
            m.away_team_goal
        FROM match m
        JOIN league l ON m.league_id = l.id
        WHERE m.season = $1 AND l.name = $2";

    let mut query = conn
        .query(
            sql,
            &[season.to_string().into(), league_name.to_string().into()],
        )
        .await?;

    let mut matches: Vec<TableMatch> = Vec::new();
    while let Some(row) = query.next().await {
        matches.push(TableMatch {
            home_team_id: row.get("home_team_api_id").unwrap_or_default(),
            away_team_id: row.get("away_team_api_id").unwrap_or_default(),
            home_team_goal: row.get("home_team_goal"),
            away_team_goal: row.get("away_team_goal"),
        });
    }

    query.result().await?;

    let table = build_result_table(&matches);
    let mut table_resources: Vec<ResultTableRowResource> = Vec::new();

    for row in table {
        let team_name = resolve_team_name(row.team_id).await?;
        table_resources.push(row.to_resource(team_name));
    }

    Ok(table_resources)
}

async fn resolve_team_name(team_id: i32) -> Result<String> {
    let request = Request::builder()
        .method(Method::GET)
        .uri(format!("http://self/teams/api-id/{}", team_id))
        .body(EmptyBody::new())?;

    print!("Request URI: {}", request.uri());

    let response: Response = spin_sdk::http::send(request).await?;

    if response.status() == StatusCode::OK {
        let body = response.into_body().bytes().await?;
        let json: Value = serde_json::from_slice(&body)?;
        let name = json["teamLongName"].as_str().unwrap_or("Unknown");
        Ok(name.to_string())
    } else {
        Ok("Unknown".to_string())
    }
}

fn internal_error_response(error: anyhow::Error) -> AxumResponse {
    AxumIntoResponse::into_response((StatusCode::INTERNAL_SERVER_ERROR, error.to_string()))
}
