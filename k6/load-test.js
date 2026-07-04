import http from 'k6/http';
import { check, randomSeed } from 'k6';
import { SharedArray } from 'k6/data';
import { randomItem } from 'https://jslib.k6.io/k6-utils/1.6.0/index.js';

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

const matchIds = new SharedArray('match IDs', function () {
    return JSON.parse(open('./pools/match-ids.json'));
});
const playerIds = new SharedArray('player IDs', function () {
    return JSON.parse(open('./pools/player-ids.json'));
});
const teamIds = new SharedArray('team IDs', function () {
    return JSON.parse(open('./pools/team-ids.json'));
});

const scenario = __ENV.scenario;
const baseUrl = 'http://hetzner-metal';

const endpoints = [
    { name: "simple", weight: 10 },
    { name: "detailed", weight: 30 },
    { name: "lookup", weight: 30 },
    { name: "aggregate", weight: 30 },
];

function pickEndpoint(randomFloat) {
    const r = randomFloat * 100;
    let acc = 0;

    for (const e of endpoints) {
        acc += e.weight;
        if (r <= acc) return e.name;
    }
    return endpoints[endpoints.length - 1].name;
}

export let options = {
    discardResponseBodies: true,
    scenarios: {},
};

const safetyThresholds = {
    http_req_failed: [{ threshold: 'rate<0.02', abortOnFail: false }],
    http_req_duration: [{ threshold: 'p(95)<10000', abortOnFail: true }],
};


if (scenario === 'coldstart') {
    options.thresholds = {
        http_req_failed: ['rate>=0'], // prevent fail on connection errors
    }
    options.scenarios.coldstart = {
        executor: 'per-vu-iterations',
        vus: 1,
        iterations: 1,
        maxDuration: '30s',
    };
} else if (scenario === 'scaling') {
    options.thresholds = safetyThresholds;
    options.scenarios.scaling = {
        executor: 'ramping-arrival-rate',
        startVUs: 10,            // How many VUs to start with
        timeUnit: '1s',          // Define rate per second
        preAllocatedVUs: 100,    // Pre-allocate VUs so k6 doesn't bottleneck allocating memory
        maxVUs: 1000,            // Upper limit of VUs allowed

        stages: [
            { duration: '1m', target: 50 },   // Baseline: Ramp up to 50 RPS (System is stable, minimal pods)
            { duration: '5m', target: 50 },   // Stay at 50 RPS
            { duration: '2m', target: 300 },  // Spike: Ramp up to 300 RPS (This should trigger HPA/KEDA scale-up)
            { duration: '5m', target: 300 },  // Sustained Load: Hold 300 RPS to see how scaled pods handle energy
            { duration: '30s', target: 0 },   // Scale Down: Drop traffic to 0 to measure cooldown energy waste
            { duration: '5m', target: 0 },    // Cooldown observation window
        ],
    };
} else if (scenario === 'baseline') {
    options.thresholds = safetyThresholds;
    options.scenarios.baseline = {
        executor: 'per-vu-iterations',
        vus: 20,
        iterations: 210,
        maxDuration: '10m',
    };
} else if (scenario === 'warmup') {
    options.scenarios.warmup = {
        executor: 'per-vu-iterations',
        vus: 20,
        iterations: 210,
        maxDuration: '30s',
    };
}



let isSeeded = false;

export default function () {
    if (!isSeeded) {
        randomSeed(__VU * 1000 + 42);
        isSeeded = true;
    }

    let response;

    if (scenario === 'scaling' || scenario === 'warmup') {
        const endpoint = pickEndpoint(Math.random());
        switch (endpoint) {

            case 'simple':
                response = http.get(`${baseUrl}/players/${randomItem(playerIds)}`);
                break;

            case 'detailed':
                response = http.get(`${baseUrl}/teams/record/${randomItem(teamIds)}`);
                break;

            case 'lookup':
                response = http.get(`${baseUrl}/match/team/${randomItem(matchIds)}`);
                break;

            case 'aggregate':
                let randomSeason = encodeURIComponent(randomItem(seasons));
                let randomLeague = encodeURIComponent(randomItem(leagues));

                response = http.get(`${baseUrl}/match/result-table?season=${randomSeason}&leagueName=${randomLeague}`);
                break;
        }
    } else if (scenario === 'coldstart') {
        response = http.get(`${baseUrl}/players/1`);
    } else if (scenario === 'baseline') {
        let randomSeason = encodeURIComponent(randomItem(seasons));
        let randomLeague = encodeURIComponent(randomItem(leagues));

        response = http.get(`${baseUrl}/match/result-table?season=${randomSeason}&leagueName=${randomLeague}`);
    }

    check(response, {
        'status is 200': (r) => r.status === 200,
    });
}