function formatDate(value) {
    if (!value) {
        return '';
    }

    if (typeof value !== 'string') {
        return '';
    }

    return value.split(' ')[0] ?? '';
}

export function playerFromRow(row) {
    return {
        id: row.id,
        apiId: row.player_api_id,
        fifaApiId: row.player_fifa_api_id,
        name: row.player_name,
        birthday: formatDate(row.birthday),
        height: row.height,
        weight: row.weight,
    };
}

export function playerAttributesFromRow(row) {
    return {
        date: formatDate(row.date),
        overallRating: row.overall_rating,
        potential: row.potential,
        preferredFoot: row.preferred_foot,
        attackingWorkRate: row.attacking_work_rate,
        defensiveWorkRate: row.defensive_work_rate,
        crossing: row.crossing,
        finishing: row.finishing,
        headingAccuracy: row.heading_accuracy,
        shortPassing: row.short_passing,
        volleys: row.volleys,
        dribbling: row.dribbling,
        curve: row.curve,
        freeKickAccuracy: row.free_kick_accuracy,
        longPassing: row.long_passing,
        ballControl: row.ball_control,
        acceleration: row.acceleration,
        sprintSpeed: row.sprint_speed,
        agility: row.agility,
        reactions: row.reactions,
        balance: row.balance,
        shotPower: row.shot_power,
        jumping: row.jumping,
        stamina: row.stamina,
        strength: row.strength,
        longShots: row.long_shots,
        aggression: row.aggression,
        interceptions: row.interceptions,
        positioning: row.positioning,
        vision: row.vision,
        penalties: row.penalties,
        marking: row.marking,
        standingTackle: row.standing_tackle,
        slidingTackle: row.sliding_tackle,
        gkDiving: row.gk_diving,
        gkHandling: row.gk_handling,
        gkKicking: row.gk_kicking,
        gkPositioning: row.gk_positioning,
        gkReflexes: row.gk_reflexes,
    };
}

export function teamFromRow(row) {
    return {
        id: row.id,
        teamApiId: row.team_api_id,
        teamFifaApiId: row.team_fifa_api_id,
        teamLongName: row.team_long_name,
        teamShortName: row.team_short_name,
    };
}

export function teamAttributesFromRow(row) {
    return {
        id: row.id,
        teamFifaApiId: row.team_fifa_api_id,
        teamApiId: row.team_api_id,
        date: formatDate(row.date),
        buildUpPlaySpeed: row.buildupplayspeed,
        buildUpPlaySpeedClass: row.buildupplayspeedclass,
        buildUpPlayDribbling: row.buildupplaydribbling,
        buildUpPlayDribblingClass: row.buildupplaydribblingclass,
        buildUpPlayPassing: row.buildupplaypassing,
        buildUpPlayPassingClass: row.buildupplaypassingclass,
        buildUpPlayPositioningClass: row.buildupplaypositioningclass,
        chanceCreationPassing: row.chancecreationpassing,
        chanceCreationPassingClass: row.chancecreationpassingclass,
        chanceCreationCrossing: row.chancecreationcrossing,
        chanceCreationCrossingClass: row.chancecreationcrossingclass,
        chanceCreationShooting: row.chancecreationshooting,
        chanceCreationShootingClass: row.chancecreationshootingclass,
        chanceCreationPositioningClass: row.chancecreationpositioningclass,
        defencePressure: row.defencepressure,
        defencePressureClass: row.defencepressureclass,
        defenceAggression: row.defenceaggression,
        defenceAggressionClass: row.defenceaggressionclass,
        defenceTeamWidth: row.defenceteamwidth,
        defenceTeamWidthClass: row.defenceteamwidthclass,
        defenceDefenderLineClass: row.defencedefenderlineclass,
    };
}

export function matchDtoFromRow(row) {
    return {
        id: row.id,
        countryId: row.country_id,
        leagueId: row.league_id,
        season: row.season,
        stage: row.stage,
        date: formatDate(row.date),
        matchApiId: row.match_api_id,
        homeTeamApiId: row.home_team_api_id,
        awayTeamApiId: row.away_team_api_id,
        homeTeamGoal: row.home_team_goal,
        awayTeamGoal: row.away_team_goal,
        homePlayerX1: row.home_player_x1,
        homePlayerX2: row.home_player_x2,
        homePlayerX3: row.home_player_x3,
        homePlayerX4: row.home_player_x4,
        homePlayerX5: row.home_player_x5,
        homePlayerX6: row.home_player_x6,
        homePlayerX7: row.home_player_x7,
        homePlayerX8: row.home_player_x8,
        homePlayerX9: row.home_player_x9,
        homePlayerX10: row.home_player_x10,
        homePlayerX11: row.home_player_x11,
        awayPlayerX1: row.away_player_x1,
        awayPlayerX2: row.away_player_x2,
        awayPlayerX3: row.away_player_x3,
        awayPlayerX4: row.away_player_x4,
        awayPlayerX5: row.away_player_x5,
        awayPlayerX6: row.away_player_x6,
        awayPlayerX7: row.away_player_x7,
        awayPlayerX8: row.away_player_x8,
        awayPlayerX9: row.away_player_x9,
        awayPlayerX10: row.away_player_x10,
        awayPlayerX11: row.away_player_x11,
    };
}

export function toMatchResource(matchDto, homeTeamName, awayTeamName) {
    return {
        matchId: matchDto.id,
        countryId: matchDto.countryId ?? 0,
        leagueId: matchDto.leagueId ?? 0,
        season: matchDto.season ?? '',
        stage: matchDto.stage ?? 0,
        date: matchDto.date ?? '',
        matchApiId: matchDto.matchApiId ?? 0,
        homeTeamId: matchDto.homeTeamApiId ?? 0,
        awayTeamId: matchDto.awayTeamApiId ?? 0,
        homeTeamName,
        awayTeamName,
        homeTeamGoal: matchDto.homeTeamGoal,
        awayTeamGoal: matchDto.awayTeamGoal,
        homePlayerLineup: {
            player1: matchDto.homePlayerX1,
            player2: matchDto.homePlayerX2,
            player3: matchDto.homePlayerX3,
            player4: matchDto.homePlayerX4,
            player5: matchDto.homePlayerX5,
            player6: matchDto.homePlayerX6,
            player7: matchDto.homePlayerX7,
            player8: matchDto.homePlayerX8,
            player9: matchDto.homePlayerX9,
            player10: matchDto.homePlayerX10,
            player11: matchDto.homePlayerX11,
        },
        awayPlayerLineup: {
            player1: matchDto.awayPlayerX1,
            player2: matchDto.awayPlayerX2,
            player3: matchDto.awayPlayerX3,
            player4: matchDto.awayPlayerX4,
            player5: matchDto.awayPlayerX5,
            player6: matchDto.awayPlayerX6,
            player7: matchDto.awayPlayerX7,
            player8: matchDto.awayPlayerX8,
            player9: matchDto.awayPlayerX9,
            player10: matchDto.awayPlayerX10,
            player11: matchDto.awayPlayerX11,
        },
    };
}
