import http from 'k6/http';
import { check } from 'k6';
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

const teamIdPool = new SharedArray('team IDs', function () {
    return JSON.parse(open('./team-ids.json'));
});

const currentScenario = __ENV.scenario;
const currentEndpoint = __ENV.endpoint;
const baseUrl = 'http://hetzner-metal';

export let options = {
    discardResponseBodies: true,
    scenarios: {},
};

const safetyThresholds = {
    http_req_failed: [{ threshold: 'rate<0.02', abortOnFail: true }],
    http_req_duration: [{ threshold: 'p(95)<2000', abortOnFail: true }],
};


if (currentScenario === 'coldstart') {
    options.thresholds = {
        http_req_failed: ['rate>=0'], // prevent fail on connection errors
    }
    options.scenarios.coldstart_profile = {
        executor: 'per-vu-iterations',
        vus: 1,
        iterations: 1,
        maxDuration: '30s',
    };
} else if (currentScenario === 'scaling') {
    options.thresholds = safetyThresholds;
    options.scenarios.autoscaling_profile = {
        executor: 'ramping-arrival-rate',
        startVUs: 10,            // How many VUs to start with
        timeUnit: '1s',          // Define rate per second
        preAllocatedVUs: 200,    // Pre-allocate VUs so k6 doesn't bottleneck allocating memory
        maxVUs: 1000,            // Upper limit of VUs allowed

        stages: [
            { duration: '2m', target: 50 },   // Baseline: Ramp up to 50 RPS (System is stable, minimal pods)
            { duration: '5m', target: 50 },   // Stay at 50 RPS
            { duration: '3m', target: 300 },  // Spike: Ramp up to 300 RPS (This should trigger HPA/KEDA scale-up)
            { duration: '5m', target: 300 },  // Sustained Load: Hold 300 RPS to see how scaled pods handle energy
            { duration: '2m', target: 0 },    // Scale Down: Drop traffic to 0 to measure cooldown energy waste
            { duration: '5m', target: 0 },    // Cooldown observation window
        ],
    };
} else if (currentScenario === 'baseline') {
    options.thresholds = safetyThresholds;
    // Default baseline: 100 VUs / 100k iterations
    options.scenarios.baseline_profile = {
        executor: 'shared-iterations',
        vus: 250,
        iterations: 100000,
        maxDuration: '10m',
    };
}

export default function () {
    let response;
    let targetId = randomItem(teamIdPool);

    switch (currentEndpoint) {

        case 'simple':
            // Workload Class: Simple read (Used for Coldstart / Minimal overhead)
            response = http.get(`${baseUrl}/players/1`);
            break;

        case 'detailed':
            // Workload Class: Detailed read (Used for Scaling / Heavy processing)
            response = http.get(`${baseUrl}/teams/record/${targetId}`);
            break;

        case 'lookup':
            // Workload Class: Lookup read (Used for Scaling / Nested DB queries)
            response = http.get(`${baseUrl}/match/team/${targetId}`);
            break;

        case 'aggregate':
            // Workload Class: Aggregate read (Used for Steady-State Baseline)
            // Picks randomized parameters on every single request to bypass database caches
            let randomSeason = encodeURIComponent(randomItem(seasons));
            let randomLeague = encodeURIComponent(randomItem(leagues));

            response = http.get(`${baseUrl}/match/result-table?season=${randomSeason}&leagueName=${randomLeague}`);
            break;

        default:
            console.error(`Unknown scenario/endpoint variable provided: "${currentEndpoint}"`);
            break;
    }

    check(response, {
        'status is 200': (r) => r.status === 200,
    });
}