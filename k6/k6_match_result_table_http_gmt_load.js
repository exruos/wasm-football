import http from "k6/http";
import { check } from "k6";
import { Trend, Counter, Rate } from "k6/metrics";

// Custom Metrics
const matchResultTableDuration = new Trend("match_result_table_duration");
const matchResultTableCount = new Counter("match_result_table_count");
const matchResultTableSuccess = new Rate("match_result_table_success");

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

  // Thresholds
  thresholds: {
    'match_result_table_duration': ['p(95)<1000', 'p(99)<1500'],
    'match_result_table_success': ['rate>0.99'],
    'http_req_duration': ['p(95)<1000', 'p(99)<1500'],
    'http_req_failed': ['rate<0.01'],
  },

  // Constant load of 400 iterations per second for 60s
  // ~24k requests in total
  scenarios: {
    match_result_table: {
      executor: "shared-iterations",
      vus: 60,
      iterations: 10000,
      maxDuration: "5m",
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

  // Record custom metrics
  matchResultTableDuration.add(res.timings.duration);
  matchResultTableCount.add(1);
  matchResultTableSuccess.add(res.status === 200);

  check(res, {
    "status is 200": (r) => r.status === 200,
  });
}