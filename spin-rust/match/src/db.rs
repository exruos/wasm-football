use chrono::NaiveDateTime;
use football_shared::domain::matches::MatchDto;
use spin_sdk::pg::Row;

pub fn match_dto_from_row(row: &Row) -> anyhow::Result<MatchDto> {
    Ok(MatchDto {
        id: row
            .get::<i32>("id")
            .ok_or_else(|| anyhow::anyhow!("missing column: id"))?,
        country_id: row.get("country_id"),
        league_id: row.get("league_id"),
        season: row.get("season"),
        stage: row.get("stage"),
        date: row
            .get::<String>("date")
            .and_then(|s| NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S").ok().map(|dt| dt.date())),
        match_api_id: row.get("match_api_id"),
        home_team_api_id: row.get("home_team_api_id"),
        away_team_api_id: row.get("away_team_api_id"),
        home_team_goal: row.get("home_team_goal"),
        away_team_goal: row.get("away_team_goal"),
        home_player_x1: row.get("home_player_x1"),
        home_player_x2: row.get("home_player_x2"),
        home_player_x3: row.get("home_player_x3"),
        home_player_x4: row.get("home_player_x4"),
        home_player_x5: row.get("home_player_x5"),
        home_player_x6: row.get("home_player_x6"),
        home_player_x7: row.get("home_player_x7"),
        home_player_x8: row.get("home_player_x8"),
        home_player_x9: row.get("home_player_x9"),
        home_player_x10: row.get("home_player_x10"),
        home_player_x11: row.get("home_player_x11"),
        away_player_x1: row.get("away_player_x1"),
        away_player_x2: row.get("away_player_x2"),
        away_player_x3: row.get("away_player_x3"),
        away_player_x4: row.get("away_player_x4"),
        away_player_x5: row.get("away_player_x5"),
        away_player_x6: row.get("away_player_x6"),
        away_player_x7: row.get("away_player_x7"),
        away_player_x8: row.get("away_player_x8"),
        away_player_x9: row.get("away_player_x9"),
        away_player_x10: row.get("away_player_x10"),
        away_player_x11: row.get("away_player_x11"),
    })
}
