use chrono::NaiveDateTime;
use serde::Serialize;
use spin_sdk::pg4::Row;

#[derive(Serialize)]
pub struct PlayerRecord {
    pub player: Player,
    pub attributes: Vec<PlayerAttributes>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Player {
    pub id: i32,
    pub api_id: i32,
    pub fifa_api_id: i32,
    pub name: String,
    pub birthday: String,
    pub height: i32,
    pub weight: i32,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayerAttributes {
    pub date: String,
    pub overall_rating: Option<i32>,
    pub potential: Option<i32>,
    pub preferred_foot: Option<String>,
    pub attacking_work_rate: Option<String>,
    pub defensive_work_rate: Option<String>,
    pub crossing: Option<i32>,
    pub finishing: Option<i32>,
    pub heading_accuracy: Option<i32>,
    pub short_passing: Option<i32>,
    pub volleys: Option<i32>,
    pub dribbling: Option<i32>,
    pub curve: Option<i32>,
    pub free_kick_accuracy: Option<i32>,
    pub long_passing: Option<i32>,
    pub ball_control: Option<i32>,
    pub acceleration: Option<i32>,
    pub sprint_speed: Option<i32>,
    pub agility: Option<i32>,
    pub reactions: Option<i32>,
    pub balance: Option<i32>,
    pub shot_power: Option<i32>,
    pub jumping: Option<i32>,
    pub stamina: Option<i32>,
    pub strength: Option<i32>,
    pub long_shots: Option<i32>,
    pub aggression: Option<i32>,
    pub interceptions: Option<i32>,
    pub positioning: Option<i32>,
    pub vision: Option<i32>,
    pub penalties: Option<i32>,
    pub marking: Option<i32>,
    pub standing_tackle: Option<i32>,
    pub sliding_tackle: Option<i32>,
    pub gk_diving: Option<i32>,
    pub gk_handling: Option<i32>,
    pub gk_kicking: Option<i32>,
    pub gk_positioning: Option<i32>,
    pub gk_reflexes: Option<i32>,
}

impl<'a> TryFrom<&'a Row<'a>> for Player {
    type Error = anyhow::Error;

    fn try_from(row: &'a Row) -> Result<Self, Self::Error> {
        Ok(Self {
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
}

impl<'a> TryFrom<&'a Row<'a>> for PlayerAttributes {
    type Error = anyhow::Error;

    fn try_from(row: &'a Row) -> Result<Self, Self::Error> {
        Ok(Self {
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
}
