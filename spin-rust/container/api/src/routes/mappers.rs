use chrono::{NaiveDate, NaiveDateTime};
use football_shared::domain::matches::MatchDto;
use football_shared::domain::players::{Player, PlayerAttributes};
use football_shared::domain::teams::{Team, TeamAttributes};
use sqlx::Row;
use sqlx::postgres::PgRow;

fn parse_date(date_text: &str) -> Option<NaiveDate> {
    NaiveDateTime::parse_from_str(date_text, "%Y-%m-%d %H:%M:%S")
        .ok()
        .map(|dt| dt.date())
}

pub(crate) fn player_from_row(row: &PgRow) -> anyhow::Result<Player> {
    Ok(Player {
        id: row.try_get("id")?,
        api_id: row.try_get("player_api_id")?,
        fifa_api_id: row.try_get("player_fifa_api_id")?,
        name: row.try_get("player_name")?,
        birthday: row
            .try_get::<String, _>("birthday")
            .unwrap_or_default()
            .split(' ')
            .next()
            .unwrap_or_default()
            .to_string(),
        height: row.try_get("height")?,
        weight: row.try_get("weight")?,
    })
}

pub(crate) fn player_attributes_from_row(row: &PgRow) -> anyhow::Result<PlayerAttributes> {
    let date_text: String = row.try_get("date")?;
    let date = parse_date(&date_text)
        .map(|d| d.to_string())
        .unwrap_or_default();

    Ok(PlayerAttributes {
        date,
        overall_rating: row.try_get("overall_rating")?,
        potential: row.try_get("potential")?,
        preferred_foot: row.try_get("preferred_foot")?,
        attacking_work_rate: row.try_get("attacking_work_rate")?,
        defensive_work_rate: row.try_get("defensive_work_rate")?,
        crossing: row.try_get("crossing")?,
        finishing: row.try_get("finishing")?,
        heading_accuracy: row.try_get("heading_accuracy")?,
        short_passing: row.try_get("short_passing")?,
        volleys: row.try_get("volleys")?,
        dribbling: row.try_get("dribbling")?,
        curve: row.try_get("curve")?,
        free_kick_accuracy: row.try_get("free_kick_accuracy")?,
        long_passing: row.try_get("long_passing")?,
        ball_control: row.try_get("ball_control")?,
        acceleration: row.try_get("acceleration")?,
        sprint_speed: row.try_get("sprint_speed")?,
        agility: row.try_get("agility")?,
        reactions: row.try_get("reactions")?,
        balance: row.try_get("balance")?,
        shot_power: row.try_get("shot_power")?,
        jumping: row.try_get("jumping")?,
        stamina: row.try_get("stamina")?,
        strength: row.try_get("strength")?,
        long_shots: row.try_get("long_shots")?,
        aggression: row.try_get("aggression")?,
        interceptions: row.try_get("interceptions")?,
        positioning: row.try_get("positioning")?,
        vision: row.try_get("vision")?,
        penalties: row.try_get("penalties")?,
        marking: row.try_get("marking")?,
        standing_tackle: row.try_get("standing_tackle")?,
        sliding_tackle: row.try_get("sliding_tackle")?,
        gk_diving: row.try_get("gk_diving")?,
        gk_handling: row.try_get("gk_handling")?,
        gk_kicking: row.try_get("gk_kicking")?,
        gk_positioning: row.try_get("gk_positioning")?,
        gk_reflexes: row.try_get("gk_reflexes")?,
    })
}

pub(crate) fn team_from_row(row: &PgRow) -> anyhow::Result<Team> {
    Ok(Team {
        id: row.try_get("id")?,
        team_api_id: row.try_get("team_api_id")?,
        team_fifa_api_id: row.try_get("team_fifa_api_id")?,
        team_long_name: row.try_get("team_long_name")?,
        team_short_name: row.try_get("team_short_name")?,
    })
}

pub(crate) fn team_attributes_from_row(row: &PgRow) -> anyhow::Result<TeamAttributes> {
    let date_text: String = row.try_get("date")?;
    let date = parse_date(&date_text)
        .map(|d| d.to_string())
        .unwrap_or_default();

    Ok(TeamAttributes {
        id: row.try_get("id")?,
        team_fifa_api_id: row.try_get("team_fifa_api_id")?,
        team_api_id: row.try_get("team_api_id")?,
        date,
        build_up_play_speed: row.try_get("buildupplayspeed")?,
        build_up_play_speed_class: row.try_get("buildupplayspeedclass")?,
        build_up_play_dribbling: row.try_get("buildupplaydribbling")?,
        build_up_play_dribbling_class: row.try_get("buildupplaydribblingclass")?,
        build_up_play_passing: row.try_get("buildupplaypassing")?,
        build_up_play_passing_class: row.try_get("buildupplaypassingclass")?,
        build_up_play_positioning_class: row.try_get("buildupplaypositioningclass")?,
        chance_creation_passing: row.try_get("chancecreationpassing")?,
        chance_creation_passing_class: row.try_get("chancecreationpassingclass")?,
        chance_creation_crossing: row.try_get("chancecreationcrossing")?,
        chance_creation_crossing_class: row.try_get("chancecreationcrossingclass")?,
        chance_creation_shooting: row.try_get("chancecreationshooting")?,
        chance_creation_shooting_class: row.try_get("chancecreationshootingclass")?,
        chance_creation_positioning_class: row.try_get("chancecreationpositioningclass")?,
        defence_pressure: row.try_get("defencepressure")?,
        defence_pressure_class: row.try_get("defencepressureclass")?,
        defence_aggression: row.try_get("defenceaggression")?,
        defence_aggression_class: row.try_get("defenceaggressionclass")?,
        defence_team_width: row.try_get("defenceteamwidth")?,
        defence_team_width_class: row.try_get("defenceteamwidthclass")?,
        defence_defender_line_class: row.try_get("defencedefenderlineclass")?,
    })
}

pub(crate) fn match_dto_from_row(row: &PgRow) -> anyhow::Result<MatchDto> {
    let date_text = row.try_get::<String, _>("date").unwrap_or_default();

    Ok(MatchDto {
        id: row.try_get("id")?,
        country_id: row.try_get("country_id")?,
        league_id: row.try_get("league_id")?,
        season: row.try_get("season")?,
        stage: row.try_get("stage")?,
        date: parse_date(&date_text),
        match_api_id: row.try_get("match_api_id")?,
        home_team_api_id: row.try_get("home_team_api_id")?,
        away_team_api_id: row.try_get("away_team_api_id")?,
        home_team_goal: row.try_get("home_team_goal")?,
        away_team_goal: row.try_get("away_team_goal")?,
        home_player_x1: row.try_get("home_player_x1")?,
        home_player_x2: row.try_get("home_player_x2")?,
        home_player_x3: row.try_get("home_player_x3")?,
        home_player_x4: row.try_get("home_player_x4")?,
        home_player_x5: row.try_get("home_player_x5")?,
        home_player_x6: row.try_get("home_player_x6")?,
        home_player_x7: row.try_get("home_player_x7")?,
        home_player_x8: row.try_get("home_player_x8")?,
        home_player_x9: row.try_get("home_player_x9")?,
        home_player_x10: row.try_get("home_player_x10")?,
        home_player_x11: row.try_get("home_player_x11")?,
        away_player_x1: row.try_get("away_player_x1")?,
        away_player_x2: row.try_get("away_player_x2")?,
        away_player_x3: row.try_get("away_player_x3")?,
        away_player_x4: row.try_get("away_player_x4")?,
        away_player_x5: row.try_get("away_player_x5")?,
        away_player_x6: row.try_get("away_player_x6")?,
        away_player_x7: row.try_get("away_player_x7")?,
        away_player_x8: row.try_get("away_player_x8")?,
        away_player_x9: row.try_get("away_player_x9")?,
        away_player_x10: row.try_get("away_player_x10")?,
        away_player_x11: row.try_get("away_player_x11")?,
    })
}
