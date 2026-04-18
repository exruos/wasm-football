use chrono::NaiveDate;
use serde::Serialize;

pub struct MatchDto {
    pub id: i32,
    pub country_id: Option<i32>,
    pub league_id: Option<i32>,
    pub season: Option<String>,
    pub stage: Option<i32>,
    pub date: Option<NaiveDate>,
    pub match_api_id: Option<i32>,
    pub home_team_api_id: Option<i32>,
    pub away_team_api_id: Option<i32>,
    pub home_team_goal: Option<i32>,
    pub away_team_goal: Option<i32>,
    pub home_player_x1: Option<i32>,
    pub home_player_x2: Option<i32>,
    pub home_player_x3: Option<i32>,
    pub home_player_x4: Option<i32>,
    pub home_player_x5: Option<i32>,
    pub home_player_x6: Option<i32>,
    pub home_player_x7: Option<i32>,
    pub home_player_x8: Option<i32>,
    pub home_player_x9: Option<i32>,
    pub home_player_x10: Option<i32>,
    pub home_player_x11: Option<i32>,
    pub away_player_x1: Option<i32>,
    pub away_player_x2: Option<i32>,
    pub away_player_x3: Option<i32>,
    pub away_player_x4: Option<i32>,
    pub away_player_x5: Option<i32>,
    pub away_player_x6: Option<i32>,
    pub away_player_x7: Option<i32>,
    pub away_player_x8: Option<i32>,
    pub away_player_x9: Option<i32>,
    pub away_player_x10: Option<i32>,
    pub away_player_x11: Option<i32>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MatchResource {
    pub match_id: i32,
    pub country_id: i32,
    pub league_id: i32,
    pub season: String,
    pub stage: i32,
    pub date: String,
    pub match_api_id: i32,
    pub home_team_id: i32,
    pub away_team_id: i32,
    pub home_team_name: String,
    pub away_team_name: String,
    pub home_team_goal: Option<i32>,
    pub away_team_goal: Option<i32>,
    pub home_player_lineup: PlayerLineup,
    pub away_player_lineup: PlayerLineup,
}

#[derive(Serialize)]
pub struct PlayerLineup {
    pub player1: Option<i32>,
    pub player2: Option<i32>,
    pub player3: Option<i32>,
    pub player4: Option<i32>,
    pub player5: Option<i32>,
    pub player6: Option<i32>,
    pub player7: Option<i32>,
    pub player8: Option<i32>,
    pub player9: Option<i32>,
    pub player10: Option<i32>,
    pub player11: Option<i32>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResultTableRowResource {
    pub team_id: i32,
    pub team_name: String,
    pub points: i32,
    pub wins: i32,
    pub draws: i32,
    pub losses: i32,
    pub goals_scored: i32,
    pub goals_conceded: i32,
}

impl MatchDto {
    pub fn to_match_resource(&self, home_team_name: String, away_team_name: String) -> MatchResource {
        MatchResource {
            match_id: self.id,
            country_id: self.country_id.unwrap_or(0),
            league_id: self.league_id.unwrap_or(0),
            season: self.season.clone().unwrap_or_default(),
            stage: self.stage.unwrap_or(0),
            date: self.date.as_ref().map(|d| d.to_string()).unwrap_or_default(),
            match_api_id: self.match_api_id.unwrap_or(0),
            home_team_id: self.home_team_api_id.unwrap_or(0),
            away_team_id: self.away_team_api_id.unwrap_or(0),
            home_team_name,
            away_team_name,
            home_team_goal: self.home_team_goal,
            away_team_goal: self.away_team_goal,
            home_player_lineup: PlayerLineup {
                player1: self.home_player_x1,
                player2: self.home_player_x2,
                player3: self.home_player_x3,
                player4: self.home_player_x4,
                player5: self.home_player_x5,
                player6: self.home_player_x6,
                player7: self.home_player_x7,
                player8: self.home_player_x8,
                player9: self.home_player_x9,
                player10: self.home_player_x10,
                player11: self.home_player_x11,
            },
            away_player_lineup: PlayerLineup {
                player1: self.away_player_x1,
                player2: self.away_player_x2,
                player3: self.away_player_x3,
                player4: self.away_player_x4,
                player5: self.away_player_x5,
                player6: self.away_player_x6,
                player7: self.away_player_x7,
                player8: self.away_player_x8,
                player9: self.away_player_x9,
                player10: self.away_player_x10,
                player11: self.away_player_x11,
            },
        }
    }
}
