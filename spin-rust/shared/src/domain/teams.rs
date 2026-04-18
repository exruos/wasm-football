use serde::Serialize;

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
