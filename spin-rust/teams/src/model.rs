use chrono::NaiveDateTime;
use serde::Serialize;
use spin_sdk::pg4::Row;

#[derive(Serialize)]
pub struct TeamRecord {
    pub team: Team,
    pub attributes: Vec<TeamAttributes>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Team {
    pub id: i32,
    pub team_api_id: i32,
    pub team_fifa_api_id: Option<i32>,
    pub team_long_name: String,
    pub team_short_name: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TeamAttributes {
    pub id: i32,
    pub team_fifa_api_id: Option<i32>,
    pub team_api_id: i32,
    pub date: String,
    pub build_up_play_speed: Option<i32>,
    pub build_up_play_speed_class: Option<String>,
    pub build_up_play_dribbling: Option<i32>,
    pub build_up_play_dribbling_class: Option<String>,
    pub build_up_play_passing: Option<i32>,
    pub build_up_play_passing_class: Option<String>,
    pub build_up_play_positioning_class: Option<String>,
    pub chance_creation_passing: Option<i32>,
    pub chance_creation_passing_class: Option<String>,
    pub chance_creation_crossing: Option<i32>,
    pub chance_creation_crossing_class: Option<String>,
    pub chance_creation_shooting: Option<i32>,
    pub chance_creation_shooting_class: Option<String>,
    pub chance_creation_positioning_class: Option<String>,
    pub defence_pressure: Option<i32>,
    pub defence_pressure_class: Option<String>,
    pub defence_aggression: Option<i32>,
    pub defence_aggression_class: Option<String>,
    pub defence_team_width: Option<i32>,
    pub defence_team_width_class: Option<String>,
    pub defence_defender_line_class: Option<String>,
}

impl<'a> TryFrom<&'a Row<'a>> for Team {
    type Error = anyhow::Error;

    fn try_from(row: &'a Row) -> Result<Self, Self::Error> {
        Ok(Self {
            id: row
                .get::<i32>("id")
                .ok_or_else(|| anyhow::anyhow!("missing column: id"))?,
            team_api_id: row
                .get::<i32>("team_api_id")
                .ok_or_else(|| anyhow::anyhow!("missing column: team_api_id"))?,
            team_fifa_api_id: row
                .get::<i32>("team_fifa_api_id"),
            team_long_name: row
                .get::<String>("team_long_name")
                .ok_or_else(|| anyhow::anyhow!("missing column: team_long_name"))?,
            team_short_name: row
                .get::<String>("team_short_name")
                .ok_or_else(|| anyhow::anyhow!("missing column: team_short_name"))?,
        })
    }
}

impl<'a> TryFrom<&'a Row<'a>> for TeamAttributes {
    type Error = anyhow::Error;

    fn try_from(row: &'a Row) -> Result<Self, Self::Error> {
        Ok(Self {
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
}