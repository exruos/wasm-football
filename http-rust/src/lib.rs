mod model;

use anyhow::{Ok, Result};
use spin_sdk::http::{Params, Request, Response, Router};
use spin_sdk::http_component;
use spin_sdk::pg4::Connection;

use crate::model::{Player, PlayerAttributes, PlayerRecord};

const DB_URL_ENV: &str = "DB_URL";

#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    router.get("/players/:id", handle_get_player);
    router.get("/players/record/:id", handle_get_player_record);

    router.handle(req)
}

fn handle_get_player(_req: Request, params: Params) -> Result<Response> {
    let address = std::env::var(DB_URL_ENV)?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let rowset = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()])?;

    match rowset.rows().next() {
        None => Ok(Response::builder().status(404).build()),
        Some(row) => {
            let player = Player::try_from(&row)?;
            let json = serde_json::to_string(&player).unwrap();
            Ok(Response::builder()
                .status(200)
                .header("Content-Type", "application/json")
                .body(json)
                .build())
        }
    }
}

fn handle_get_player_record(_req: Request, params: Params) -> Result<Response> {
    let address = std::env::var(DB_URL_ENV)?;
    let conn = Connection::open(&address)?;

    let id = params.get("id").unwrap_or("0").parse::<i32>()?;

    let player_rowset = conn.query("SELECT * FROM player WHERE id = $1", &[id.into()])?;

    let player = match player_rowset.rows().next() {
        None => return Ok(Response::builder().status(404).build()),
        Some(row) => Player::try_from(&row)?,
    };

    let attributes_rowset = conn.query(
        "SELECT * FROM player_attributes WHERE player_api_id = $1 AND player_fifa_api_id = $2",
        &[player.api_id.into(), player.fifa_api_id.into()],
    )?;

    let mut attributes = Vec::new();
    for row in attributes_rowset.rows() {
        attributes.push(PlayerAttributes::try_from(&row)?);
    }

    let player_record = PlayerRecord { player, attributes };

    let json = serde_json::to_string(&player_record)?;
    Ok(Response::builder()
        .status(200)
        .header("Content-Type", "application/json")
        .body(json)
        .build())
}
