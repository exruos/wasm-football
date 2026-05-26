mod db;

use anyhow::{Ok, Result};
use football_shared::domain::teams::TeamRecord;
use spin_sdk::http::{Params, Request, Response, Router};
#[cfg(feature = "spin-component")]
use spin_sdk::http_component;
use spin_sdk::pg4::Connection;
use spin_sdk::variables;

use crate::db::{team_attributes_from_row, team_from_row};

pub fn register_routes(router: &mut Router) {
    router.get("/teams/:id", get_team_by_id);
    router.get("/teams/api-id/:id", get_team_by_api_id);
    router.get("/teams/record/:id", get_team_record_by_id);
}

#[cfg(feature = "spin-component")]
#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    register_routes(&mut router);
    router.any("/*", |_: Request, _| {
        Ok(Response::builder()
            .status(404)
            .header("Content-Type", "application/json")
            .body("{\"status\":404,\"error\":\"Not Found\"}".to_string())
            .build())
    });

    router.handle(req)
}

fn get_team_by_id(_req: Request, params: Params) -> Result<Response> {
    let address = variables::get("db_url")?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let rowset = conn.query("SELECT * FROM team WHERE id = $1", &[id.into()])?;

    match rowset.rows().next() {
        None => Ok(Response::builder().status(404).build()),
        Some(row) => {
            let team = team_from_row(&row)?;
            let json = serde_json::to_string(&team).unwrap();
            Ok(Response::builder()
                .status(200)
                .header("Content-Type", "application/json")
                .body(json)
                .build())
        }
    }
}

fn get_team_by_api_id(_req: Request, params: Params) -> Result<Response> {
    let address = variables::get("db_url")?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let rowset = conn.query("SELECT * FROM team WHERE team_api_id = $1", &[id.into()])?;

    match rowset.rows().next() {
        None => Ok(Response::builder().status(404).build()),
        Some(row) => {
            let team = team_from_row(&row)?;
            let json = serde_json::to_string(&team).unwrap();
            Ok(Response::builder()
                .status(200)
                .header("Content-Type", "application/json")
                .body(json)
                .build())
        }
    }
}

fn get_team_record_by_id(_req: Request, params: Params) -> Result<Response> {
    let address = variables::get("db_url")?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let team_rowset = conn.query("SELECT * FROM team WHERE team_api_id = $1", &[id.into()])?;

    let team = match team_rowset.rows().next() {
        None => return Ok(Response::builder().status(404).build()),
        Some(row) => team_from_row(&row)?,
    };

    let attributes_rowset = conn.query(
        "SELECT * FROM team_attributes WHERE team_api_id = $1 AND team_fifa_api_id = $2",
        &[team.team_api_id.into(), team.team_fifa_api_id.into()],
    )?;

    let mut attributes = Vec::new();
    for row in attributes_rowset.rows() {
        attributes.push(team_attributes_from_row(&row)?);
    }

    let team_record = TeamRecord { team, attributes };

    let json = serde_json::to_string(&team_record)?;
    Ok(Response::builder()
        .status(200)
        .header("Content-Type", "application/json")
        .body(json)
        .build())
}
