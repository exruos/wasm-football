use chrono::NaiveDateTime;
use football_shared::domain::teams::{Team, TeamAttributes};
use spin_sdk::pg::Row;

pub fn team_from_row(row: &Row) -> anyhow::Result<Team> {
    Ok(Team {
        id: row
            .get::<i32>("id")
            .ok_or_else(|| anyhow::anyhow!("missing column: id"))?,
        team_api_id: row
            .get::<i32>("team_api_id")
            .ok_or_else(|| anyhow::anyhow!("missing column: team_api_id"))?,
        team_fifa_api_id: row.get::<i32>("team_fifa_api_id"),
        team_long_name: row
            .get::<String>("team_long_name")
            .ok_or_else(|| anyhow::anyhow!("missing column: team_long_name"))?,
        team_short_name: row
            .get::<String>("team_short_name")
            .ok_or_else(|| anyhow::anyhow!("missing column: team_short_name"))?,
    })
}

pub fn team_attributes_from_row(row: &Row) -> anyhow::Result<TeamAttributes> {
    Ok(TeamAttributes {
        id: row
            .get::<i32>("id")
            .ok_or_else(|| anyhow::anyhow!("missing column: id"))?,
        team_fifa_api_id: row.get::<i32>("team_fifa_api_id"),
        team_api_id: row
            .get::<i32>("team_api_id")
            .ok_or_else(|| anyhow::anyhow!("missing column: team_api_id"))?,
        date: row
            .get::<String>("date")
            .ok_or_else(|| anyhow::anyhow!("missing column: date"))
            .and_then(|s| {
                NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S")
                    .map(|dt| dt.date().to_string())
                    .map_err(|e| anyhow::anyhow!("DateTime parse error: {}", e))
            })?,
        build_up_play_speed: row.get("buildupplayspeed"),
        build_up_play_speed_class: row.get("buildupplayspeedclass"),
        build_up_play_dribbling: row.get("buildupplaydribbling"),
        build_up_play_dribbling_class: row.get("buildupplaydribblingclass"),
        build_up_play_passing: row.get("buildupplaypassing"),
        build_up_play_passing_class: row.get("buildupplaypassingclass"),
        build_up_play_positioning_class: row.get("buildupplaypositioningclass"),
        chance_creation_passing: row.get("chancecreationpassing"),
        chance_creation_passing_class: row.get("chancecreationpassingclass"),
        chance_creation_crossing: row.get("chancecreationcrossing"),
        chance_creation_crossing_class: row.get("chancecreationcrossingclass"),
        chance_creation_shooting: row.get("chancecreationshooting"),
        chance_creation_shooting_class: row.get("chancecreationshootingclass"),
        chance_creation_positioning_class: row.get("chancecreationpositioningclass"),
        defence_pressure: row.get("defencepressure"),
        defence_pressure_class: row.get("defencepressureclass"),
        defence_aggression: row.get("defenceaggression"),
        defence_aggression_class: row.get("defenceaggressionclass"),
        defence_team_width: row.get("defenceteamwidth"),
        defence_team_width_class: row.get("defenceteamwidthclass"),
        defence_defender_line_class: row.get("defencedefenderlineclass"),
    })
}
