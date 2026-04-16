import http from "k6/http";
import { check } from "k6";

// Iterations: 36k
export const options = {
  discardResponseBodies: true,

  scenarios: {
    player: {
      executor: "ramping-arrival-rate",

      // Start iterations per `timeUnit`
      startRate: 1,

      // Start `startRate` iterations per second
      timeUnit: "1s",

      // Pre-allocate necessary VUs.
      preAllocatedVUs: 200,

      stages: [
        // Ramp-up to 400 iterations per second
        { target: 400, duration: "30s" },

        // Continue starting 400 iterations per second for one minute.
        { target: 400, duration: "1m" },

        // Linearly ramp-down to starting 1 iteration per second.
        { target: 1, duration: "30s" },
      ],
    },
  },
};

export default function () {
  const hostname = `${__ENV.TARGET_HOSTNAME}`;
  const port = `${__ENV.TARGET_PORT}`;
  const randomId = Math.floor(Math.random() * 11075) + 1;

  // Get player
  const res1 = http.get(`http://${hostname}:${port}/players/${randomId}`, {
    tags: { name: "player" },
  });
  check(res1, {
    "status is 200": (r) => r.status === 200,
    "protocol is HTTP/2": (r) => r.proto === 'HTTP/2.0',
  });

  // Get player record
  const res2 = http.get(`http://${hostname}:${port}/players/record/${randomId}`, {
    tags: { name: "player_record" },
  });
  check(res2, {
    "status is 200": (r) => r.status === 200,
    "protocol is HTTP/2": (r) => r.proto === 'HTTP/2.0',
  });
}
