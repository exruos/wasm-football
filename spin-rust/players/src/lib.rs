mod db;

use anyhow::{Ok, Result};
use football_shared::domain::players::PlayerRecord;
use spin_sdk::http::{Params, Request, Response, Router};
use spin_sdk::variables;
#[cfg(feature = "spin-component")]
use spin_sdk::http_component;
use spin_sdk::pg4::Connection;

use crate::db::{player_attributes_from_row, player_from_row};

pub fn register_routes(router: &mut Router) {
    router.get("/players/:id", get_player_by_id);
    router.get("/players/record/:id", get_player_record_by_id);
}

#[cfg(feature = "spin-component")]
#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    register_routes(&mut router);

    router.handle(req)
}

fn get_player_by_id(_req: Request, params: Params) -> Result<Response> {
    let address = variables::get("db_url")?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let rowset = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()])?;

    match rowset.rows().next() {
        None => Ok(Response::builder().status(404).build()),
        Some(row) => {
            let player = player_from_row(&row)?;
            let json = serde_json::to_string(&player).unwrap();
            Ok(Response::builder()
                .status(200)
                .header("Content-Type", "application/json")
                .body(json)
                .build())
        }
    }
}

fn get_player_record_by_id(_req: Request, params: Params) -> Result<Response> {
    let address = variables::get("db_url")?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let player_rowset = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()])?;

    let player = match player_rowset.rows().next() {
        None => return Ok(Response::builder().status(404).build()),
        Some(row) => player_from_row(&row)?,
    };

    let attributes_rowset = conn.query(
        "SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2",
        &[player.api_id.into(), player.fifa_api_id.into()],
    )?;

    let mut attributes = Vec::new();
    for row in attributes_rowset.rows() {
        attributes.push(player_attributes_from_row(&row)?);
    }

    let player_record = PlayerRecord { player, attributes };

    let json = serde_json::to_string(&player_record)?;
    Ok(Response::builder()
        .status(200)
        .header("Content-Type", "application/json")
        .body(json)
        .build())
}
