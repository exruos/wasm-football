use serde::Serialize;

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
