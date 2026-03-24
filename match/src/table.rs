use std::collections::HashMap;

use crate::model::ResultTableRowResource;

const POINTS_FOR_WIN: i32 = 3;
const POINTS_FOR_DRAW: i32 = 1;

#[derive(Default)]
struct TeamStatistics {
    points: i32,
    wins: i32,
    draws: i32,
    losses: i32,
    goals_scored: i32,
    goals_conceded: i32,
}

impl TeamStatistics {
    fn record_match(&mut self, scored: i32, conceded: i32) {
        self.goals_scored += scored;
        self.goals_conceded += conceded;
        if scored > conceded {
            self.wins += 1;
            self.points += POINTS_FOR_WIN;
        } else if scored == conceded {
            self.draws += 1;
            self.points += POINTS_FOR_DRAW;
        } else {
            self.losses += 1;
        }
    }
}

pub struct ResultTableRow {
    pub team_id: i32,
    pub points: i32,
    pub wins: i32,
    pub draws: i32,
    pub losses: i32,
    pub goals_scored: i32,
    pub goals_conceded: i32,
}

impl ResultTableRow {
    pub fn to_resource(&self, team_name: String) -> ResultTableRowResource {
        ResultTableRowResource {
            team_id: self.team_id,
            team_name,
            points: self.points,
            wins: self.wins,
            draws: self.draws,
            losses: self.losses,
            goals_scored: self.goals_scored,
            goals_conceded: self.goals_conceded,
        }
    }
}

pub struct Match {
    pub home_team_id: i32,
    pub away_team_id: i32,
    pub home_team_goal: Option<i32>,
    pub away_team_goal: Option<i32>,
}

#[allow(dead_code)]
fn read_matches_for_result_table(season: &str, league_name: &str) -> Vec<Match> {
    println!("Reading matches for {} - {}", season, league_name);
    vec![]
}

pub fn build_result_table(matches: &[Match]) -> Vec<ResultTableRow> {
    let mut team_stats: HashMap<i32, TeamStatistics> = HashMap::new();

    for m in matches {
        if let (Some(home_goals), Some(away_goals)) = (m.home_team_goal, m.away_team_goal) {
            // Update Home Team
            team_stats.entry(m.home_team_id).or_default().record_match(home_goals, away_goals);
            // Update Away Team
            team_stats.entry(m.away_team_id).or_default().record_match(away_goals, home_goals);
        }
    }

    let mut table: Vec<ResultTableRow> = team_stats
        .into_iter()
        .map(|(team_id, stats)| ResultTableRow {
            team_id,
            points: stats.points,
            wins: stats.wins,
            draws: stats.draws,
            losses: stats.losses,
            goals_scored: stats.goals_scored,
            goals_conceded: stats.goals_conceded,
        })
        .collect();

    table.sort_by(|a, b| {
        b.points
            .cmp(&a.points) // Descending points
            .then_with(|| {
                let a_diff = a.goals_scored - a.goals_conceded;
                let b_diff = b.goals_scored - b.goals_conceded;
                b_diff.cmp(&a_diff) // Descending goal difference
            })
            .then_with(|| b.goals_scored.cmp(&a.goals_scored)) // Descending goals scored
    });

    table
}
