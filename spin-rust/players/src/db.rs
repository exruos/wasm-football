use chrono::NaiveDateTime;
use football_shared::domain::players::{Player, PlayerAttributes};
use spin_sdk::pg4::Row;

pub fn player_from_row(row: &Row) -> anyhow::Result<Player> {
    Ok(Player {
        id: row
            .get::<i32>("id")
            .ok_or_else(|| anyhow::anyhow!("missing column: id"))?,
        api_id: row
            .get::<i32>("player_api_id")
            .ok_or_else(|| anyhow::anyhow!("missing column: api_id"))?,
        fifa_api_id: row
            .get::<i32>("player_fifa_api_id")
            .ok_or_else(|| anyhow::anyhow!("missing column: fifa_api_id"))?,
        name: row
            .get::<String>("player_name")
            .ok_or_else(|| anyhow::anyhow!("missing column: name"))?,
        birthday: row
            .get::<String>("birthday")
            .ok_or_else(|| anyhow::anyhow!("missing column: birthday"))
            .and_then(|s| {
                NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S")
                    .map(|dt| dt.date())
                    .map_err(|e| anyhow::anyhow!("DateTime parse error: {}", e))
            })?
            .to_string(),
        height: row
            .get::<i32>("height")
            .ok_or_else(|| anyhow::anyhow!("missing column: height"))?,
        weight: row
            .get::<i32>("weight")
            .ok_or_else(|| anyhow::anyhow!("missing column: weight"))?,
    })
}

pub fn player_attributes_from_row(row: &Row) -> anyhow::Result<PlayerAttributes> {
    Ok(PlayerAttributes {
        date: row
            .get::<String>("date")
            .ok_or_else(|| anyhow::anyhow!("missing column: date"))
            .and_then(|s| {
                NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S")
                    .map(|dt| dt.date())
                    .map_err(|e| anyhow::anyhow!("DateTime parse error: {}", e))
            })?
            .to_string(),
        overall_rating: row.get::<i32>("overall_rating"),
        potential: row.get::<i32>("potential"),
        preferred_foot: row.get::<String>("preferred_foot"),
        attacking_work_rate: row.get::<String>("attacking_work_rate"),
        defensive_work_rate: row.get::<String>("defensive_work_rate"),
        crossing: row.get::<i32>("crossing"),
        finishing: row.get::<i32>("finishing"),
        heading_accuracy: row.get::<i32>("heading_accuracy"),
        short_passing: row.get::<i32>("short_passing"),
        volleys: row.get::<i32>("volleys"),
        dribbling: row.get::<i32>("dribbling"),
        curve: row.get::<i32>("curve"),
        free_kick_accuracy: row.get::<i32>("free_kick_accuracy"),
        long_passing: row.get::<i32>("long_passing"),
        ball_control: row.get::<i32>("ball_control"),
        acceleration: row.get::<i32>("acceleration"),
        sprint_speed: row.get::<i32>("sprint_speed"),
        agility: row.get::<i32>("agility"),
        reactions: row.get::<i32>("reactions"),
        balance: row.get::<i32>("balance"),
        shot_power: row.get::<i32>("shot_power"),
        jumping: row.get::<i32>("jumping"),
        stamina: row.get::<i32>("stamina"),
        strength: row.get::<i32>("strength"),
        long_shots: row.get::<i32>("long_shots"),
        aggression: row.get::<i32>("aggression"),
        interceptions: row.get::<i32>("interceptions"),
        positioning: row.get::<i32>("positioning"),
        vision: row.get::<i32>("vision"),
        penalties: row.get::<i32>("penalties"),
        marking: row.get::<i32>("marking"),
        standing_tackle: row.get::<i32>("standing_tackle"),
        sliding_tackle: row.get::<i32>("sliding_tackle"),
        gk_diving: row.get::<i32>("gk_diving"),
        gk_handling: row.get::<i32>("gk_handling"),
        gk_kicking: row.get::<i32>("gk_kicking"),
        gk_positioning: row.get::<i32>("gk_positioning"),
        gk_reflexes: row.get::<i32>("gk_reflexes"),
    })
}
