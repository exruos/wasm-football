const POINTS_FOR_WIN = 3;
const POINTS_FOR_DRAW = 1;

export function buildResultTable(matches) {
    const teamStats = new Map();

    const recordMatch = (teamId, scored, conceded) => {
        const current = teamStats.get(teamId) ?? {
            points: 0,
            wins: 0,
            draws: 0,
            losses: 0,
            goalsScored: 0,
            goalsConceded: 0,
        };

        current.goalsScored += scored;
        current.goalsConceded += conceded;

        if (scored > conceded) {
            current.wins += 1;
            current.points += POINTS_FOR_WIN;
        } else if (scored === conceded) {
            current.draws += 1;
            current.points += POINTS_FOR_DRAW;
        } else {
            current.losses += 1;
        }

        teamStats.set(teamId, current);
    };

    for (const match of matches) {
        if (match.homeTeamGoal == null || match.awayTeamGoal == null) {
            continue;
        }

        recordMatch(match.homeTeamId, match.homeTeamGoal, match.awayTeamGoal);
        recordMatch(match.awayTeamId, match.awayTeamGoal, match.homeTeamGoal);
    }

    return [...teamStats.entries()]
        .map(([teamId, stats]) => ({
            teamId,
            points: stats.points,
            wins: stats.wins,
            draws: stats.draws,
            losses: stats.losses,
            goalsScored: stats.goalsScored,
            goalsConceded: stats.goalsConceded,
        }))
        .sort((a, b) => {
            if (b.points !== a.points) {
                return b.points - a.points;
            }

            const goalDiffA = a.goalsScored - a.goalsConceded;
            const goalDiffB = b.goalsScored - b.goalsConceded;
            if (goalDiffB !== goalDiffA) {
                return goalDiffB - goalDiffA;
            }

            return b.goalsScored - a.goalsScored;
        });
}
