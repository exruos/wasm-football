mod model;
mod table;

use anyhow::{Ok, Result};
use serde_json::Value;
use spin_sdk::http::{Method, Params, Request, Response, Router};
use spin_sdk::http_component;
use spin_sdk::pg4::Connection;
use url::Url;

use crate::model::{MatchDto, MatchResource, ResultTableRowResource};
use crate::table::{Match, build_result_table};

const DB_URL_ENV: &str = "DB_URL";

#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    router.get_async("/match/:id", get_match_by_id);
    router.get_async("/match/team/:id", get_matches_by_team_id);
    router.get_async("/match/result-table", get_result_table_by_season_and_league);

    router.handle(req)
}

async fn get_match_by_id(_req: Request, params: Params) -> Result<Response> {
    let address = std::env::var(DB_URL_ENV)?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let sql = "SELECT id, country_id, league_id, season, stage, date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE id = $1";

    let rowset = conn.query(sql, &[id.into()])?;

    match rowset.rows().next() {
        None => Ok(Response::builder().status(404).build()),
        Some(row) => {
            let match_dto = MatchDto::try_from(&row)?;

            let home_team_name = resolve_team_name(match_dto.home_team_api_id.unwrap_or(0)).await?;
            let away_team_name = resolve_team_name(match_dto.away_team_api_id.unwrap_or(0)).await?;

            let match_resource = match_dto.to_match_resource(home_team_name, away_team_name);
            let json = serde_json::to_string(&match_resource).unwrap();
            Ok(Response::builder()
                .status(200)
                .header("Content-Type", "application/json")
                .body(json)
                .build())
        }
    }
}

async fn get_matches_by_team_id(_req: Request, params: Params) -> Result<Response> {
    let address = std::env::var(DB_URL_ENV)?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let sql = "SELECT id, country_id, league_id, season, stage, date, match_api_id, home_team_api_id, away_team_api_id, home_team_goal, away_team_goal, home_player_x1, home_player_x2, home_player_x3, home_player_x4, home_player_x5, home_player_x6, home_player_x7, home_player_x8, home_player_x9, home_player_x10, home_player_x11, away_player_x1, away_player_x2, away_player_x3, away_player_x4, away_player_x5, away_player_x6, away_player_x7, away_player_x8, away_player_x9, away_player_x10, away_player_x11 FROM match WHERE home_team_api_id = $1 OR away_team_api_id = $1";

    let rowset = conn.query(sql, &[id.into()])?;

    let mut matches: Vec<MatchResource> = Vec::new();

    for row in rowset.rows() {
        let match_dto = MatchDto::try_from(&row)?;

        let home_team_name = resolve_team_name(match_dto.home_team_api_id.unwrap_or(0)).await?;
        let away_team_name = resolve_team_name(match_dto.away_team_api_id.unwrap_or(0)).await?;

        let match_resource = match_dto.to_match_resource(home_team_name, away_team_name);
        matches.push(match_resource);
    }

    if matches.is_empty() {
        Ok(Response::builder().status(404).build())
    } else {
        let json = serde_json::to_string(&matches).unwrap();
        Ok(Response::builder()
            .status(200)
            .header("Content-Type", "application/json")
            .body(json)
            .build())
    }
}

async fn get_result_table_by_season_and_league(req: Request, _params: Params) -> Result<Response> {
    let address = std::env::var(DB_URL_ENV)?;
    let conn = Connection::open(&address)?;

    let url = Url::parse(&format!("http://localhost{}", req.uri()))?;

    let query_params: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();

    if !query_params.contains_key("season") || !query_params.contains_key("leagueName") {
        return Ok(Response::builder()
            .status(400)
            .body("Missing required query parameters: season and leagueName")
            .build());
    }

    let season = query_params.get("season").map(|s| s.as_str()).unwrap_or("");
    let league_name = query_params
        .get("leagueName")
        .map(|s| s.as_str())
        .unwrap_or("");

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

    let rowset = conn.query(
        sql,
        &[season.to_string().into(), league_name.to_string().into()],
    )?;

    let matches: Vec<Match> = rowset
        .rows()
        .map(|row| Match {
            home_team_id: row.get("home_team_api_id").unwrap_or_default(),
            away_team_id: row.get("away_team_api_id").unwrap_or_default(),
            home_team_goal: row.get("home_team_goal"),
            away_team_goal: row.get("away_team_goal"),
        })
        .collect();

    let table = build_result_table(&matches);
    let mut table_resources: Vec<ResultTableRowResource> = Vec::new();

    for row in table {
        let team_name = resolve_team_name(row.team_id).await?;
        let row_resource = row.to_resource(team_name);
        table_resources.push(row_resource);
    }

    Ok(Response::builder()
        .status(200)
        .header("Content-Type", "application/json")
        .body(serde_json::to_string(&table_resources).unwrap())
        .build())
}

async fn resolve_team_name(team_id: i32) -> Result<String> {
    let request = Request::builder()
        .method(Method::Get)
        .uri(format!("/teams/api-id/{}", team_id))
        .build();

    let response: Response = spin_sdk::http::send(request).await?;

    if *response.status() == 200 {
        let body = response.body();
        let json: Value = serde_json::from_slice(body)?;
        let name = json["teamLongName"].as_str().unwrap_or("Unknown");
        Ok(name.to_string())
    } else {
        Ok("Unknown".to_string())
    }
}
