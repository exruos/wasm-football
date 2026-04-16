import http from "k6/http";
import { check } from "k6";

const seasons = [
  "2012/2013",
  "2010/2011",
  "2013/2014",
  "2015/2016",
  "2008/2009",
  "2014/2015",
  "2011/2012",
  "2009/2010"
];

const leagues = [
  "Belgium Jupiler league",
  "England Premier league",
  "France Ligue 1",
  "Germany 1. Bundesliga",
  "Italy Serie A",
  "Netherlands Eredivisie",
  "Poland Ekstraklasa",
  "Portugal Liga ZON Sagres",
  "Scotland Premier league",
  "Spain LIGA BBVA",
  "Switzerland Super league"
];

export const options = {
  discardResponseBodies: true,

  // Ramp-up to 400 iterations per second in 30s
  // ~6k requests in total
  scenarios: {
    match_result_table: {
      executor: "ramping-arrival-rate",
      startRate: 1,
      timeUnit: "1s",
      preAllocatedVUs: 60,
      stages: [{ target: 400, duration: "30s" }],
    },
  },
};

export default function () {
  const hostname = `${__ENV.TARGET_HOSTNAME}`;
  const port = `${__ENV.TARGET_PORT}`;

  const randomSeason = seasons[Math.floor(Math.random() * seasons.length)];
  const randomLeague = leagues[Math.floor(Math.random() * leagues.length)];

  const res = http.get(
    `http://${hostname}:${port}/match/result-table?season=${encodeURIComponent(randomSeason)}&leagueName=${encodeURIComponent(randomLeague)}`,
    {
      tags: { name: "match_result_table" },
    }
  );
  check(res, {
    "status is 200": (r) => r.status === 200,
  });
}