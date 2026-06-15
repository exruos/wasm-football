import http from 'k6/http';
import { sleep } from 'k6';

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

const currentScenario = __ENV.thesis_scenario;
const currentEndpoint = __ENV.thesis_endpoint;

export let options = {
    discardResponseBodies: true,
    scenarios: {},
};

const safetyThresholds = {
  http_req_failed: [{ threshold: 'rate<0.02', abortOnFail: true }],
  http_req_duration: [{ threshold: 'p(95)<2000', abortOnFail: true }],
};


if (currentScenario === 'coldstart') {
    options.scenarios.coldstart_profile = {
        executor: 'per-vu-iterations',
        vus: 50,          // Number of VUs to simulate concurrent cold starts
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
        vus: 100,
        iterations: 1000000,
        maxDuration: '10m',
    };
}

export default function () {
    const baseUrl = 'http://thesis-ingress-target';
    let targetUrl = '';

    // TODO: randomize endpoints based on other scripts
    if (currentEndpoint === 'simple') {
        targetUrl = `${baseUrl}/players/42`;
    } else if (currentEndpoint === 'lookup') {
        targetUrl = `${baseUrl}/match/team/99`;
    } else if (currentEndpoint === 'aggregate') {
        targetUrl = `${baseUrl}/match/result-table?season=2026&leagueName=Bundesliga`;
    }

    http.get(targetUrl);
    sleep(0.01);
}