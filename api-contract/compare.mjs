import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const DEFAULT_FIXTURES_FILE = new URL('./fixtures/endpoints.json', import.meta.url);
const DEFAULT_TARGET_URLS = ['http://localhost:8088'];
const DEFAULT_TIMEOUT_MS = 10_000;

function printHelp() {
  console.log(`Usage:
  npm run compare -- [--target <baseUrl>]... [--fixtures <path>] [--artifacts-dir <path>] [--timeout-ms <ms>]

Examples:
  npm run compare -- --target http://localhost:8088 --target http://localhost:8089
  npm run compare -- --fixtures ./fixtures/endpoints.json --artifacts-dir ./artifacts/latest
`);
}

function parseArgs(argv) {
  const args = {
    targets: [],
    fixturesFile: null,
    artifactsDir: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];

    if (argument === '--help' || argument === '-h') {
      args.help = true;
      continue;
    }

    if (argument === '--target') {
      const value = argv[++index];
      if (!value) {
        throw new Error('Missing value for --target');
      }
      args.targets.push(value);
      continue;
    }

    if (argument === '--fixtures') {
      const value = argv[++index];
      if (!value) {
        throw new Error('Missing value for --fixtures');
      }
      args.fixturesFile = value;
      continue;
    }

    if (argument === '--artifacts-dir') {
      const value = argv[++index];
      if (!value) {
        throw new Error('Missing value for --artifacts-dir');
      }
      args.artifactsDir = value;
      continue;
    }

    if (argument === '--timeout-ms') {
      const value = argv[++index];
      if (!value) {
        throw new Error('Missing value for --timeout-ms');
      }
      const parsedTimeout = Number(value);
      if (!Number.isFinite(parsedTimeout) || parsedTimeout <= 0) {
        throw new Error('The timeout must be a positive number of milliseconds');
      }
      args.timeoutMs = parsedTimeout;
      continue;
    }

    throw new Error(`Unknown argument: ${argument}`);
  }

  return args;
}

function resolveTargetDefinitions(argsTargets) {
  const envTargets = process.env.API_TARGETS
    ? process.env.API_TARGETS.split(',').map((value) => value.trim()).filter(Boolean)
    : [];

  const targetUrls = [...argsTargets, ...envTargets];

  if (targetUrls.length === 0) {
    targetUrls.push(...DEFAULT_TARGET_URLS);
  }

  return targetUrls.map((baseUrl, index) => ({
    name: `target-${index + 1}`,
    baseUrl,
  }));
}

async function readJsonFile(filePath) {
  const fileContents = await fs.readFile(filePath, 'utf8');
  return JSON.parse(fileContents);
}

function buildPath(pathTemplate, params = {}) {
  return pathTemplate.replace(/\{([^}]+)\}/g, (_, key) => {
    if (!(key in params)) {
      throw new Error(`Missing path parameter: ${key}`);
    }

    return encodeURIComponent(String(params[key]));
  });
}

function looksLikeJson(text) {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[');
}

function normalizeResponseBody(text, contentType) {
  if (contentType.includes('application/json') || looksLikeJson(text)) {
    try {
      return {
        kind: 'json',
        value: JSON.parse(text),
      };
    } catch {
      // Fall through to plain text comparison when parsing fails.
    }
  }

  return {
    kind: 'text',
    value: text.trimEnd(),
  };
}

function normalizeResponse(response, bodyText) {
  const rawContentType = response.headers.get('content-type') ?? '';
  const contentType = rawContentType.split(';')[0].trim().toLowerCase();

  return {
    status: response.status,
    contentType,
    body: normalizeResponseBody(bodyText, contentType),
  };
}

function stringifyNormalizedResponse(response) {
  return JSON.stringify(response, null, 2);
}

function renderDiff(expected, actual) {
  const expectedLines = expected.split('\n');
  const actualLines = actual.split('\n');
  const maxLines = Math.max(expectedLines.length, actualLines.length);

  for (let index = 0; index < maxLines; index += 1) {
    const expectedLine = expectedLines[index];
    const actualLine = actualLines[index];

    if (expectedLine !== actualLine) {
      const contextStart = Math.max(0, index - 2);
      const contextEnd = Math.min(maxLines, index + 3);
      const output = [];

      for (let contextIndex = contextStart; contextIndex < contextEnd; contextIndex += 1) {
        const referenceLine = expectedLines[contextIndex];
        const candidateLine = actualLines[contextIndex];

        if (referenceLine === candidateLine) {
          output.push(`  ${referenceLine ?? ''}`);
        } else {
          if (referenceLine !== undefined) {
            output.push(`- ${referenceLine}`);
          }
          if (candidateLine !== undefined) {
            output.push(`+ ${candidateLine}`);
          }
        }
      }

      return output.join('\n');
    }
  }

  return 'No inline diff available; the normalized payloads still differ.';
}

async function writeArtifact(artifactsDir, endpointName, targetName, payload) {
  if (!artifactsDir) {
    return;
  }

  const endpointDir = path.join(artifactsDir, endpointName);
  await fs.mkdir(endpointDir, { recursive: true });
  const artifactPath = path.join(endpointDir, `${targetName}.json`);
  await fs.writeFile(artifactPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

function getEndpointLabel(endpoint) {
  return endpoint.name ?? endpoint.pathTemplate;
}

async function fetchEndpoint(target, endpoint, timeoutMs) {
  const url = new URL(buildPath(endpoint.pathTemplate, endpoint.params ?? {}), target.baseUrl);

  if (endpoint.query) {
    for (const [key, value] of Object.entries(endpoint.query)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(new Error(`Request timed out after ${timeoutMs}ms`)), timeoutMs);

  try {
    const response = await fetch(url, {
      method: endpoint.method ?? 'GET',
      signal: controller.signal,
    });

    const bodyText = await response.text();
    return {
      url: url.toString(),
      raw: {
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
        body: bodyText,
      },
      normalized: normalizeResponse(response, bodyText),
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fetchResponsesForEndpoint(endpoint, targets, timeoutMs, artifactsDir) {
  const endpointLabel = getEndpointLabel(endpoint);
  console.log(`\n[${endpointLabel}]`);

  const responses = [];
  for (const target of targets) {
    const response = await fetchEndpoint(target, endpoint, timeoutMs);
    responses.push({ target, ...response });
    await writeArtifact(artifactsDir, endpointLabel, target.name, response);
    console.log(`  ${target.name}: ${response.normalized.status} ${response.url}`);
  }

  return { endpointLabel, responses };
}

function compareEndpointResponses(endpointLabel, responses, failures) {
  const reference = responses[0].normalized;

  for (let index = 1; index < responses.length; index += 1) {
    const candidate = responses[index];
    const comparison = compareResponses(reference, candidate.normalized);

    if (!comparison.equal) {
      failures.push({
        endpoint: endpointLabel,
        reference: responses[0].target,
        candidate: candidate.target,
        reason: comparison.reason,
        diff: comparison.diff,
      });
      console.log(`  FAIL ${candidate.target.name}: ${comparison.reason}`);
      if (comparison.diff) {
        console.log(comparison.diff);
      }
    } else {
      console.log(`  PASS ${candidate.target.name}`);
    }
  }
}

function compareResponses(reference, candidate) {
  if (reference.status !== candidate.status) {
    return {
      equal: false,
      reason: `status ${reference.status} !== ${candidate.status}`,
    };
  }

  if (reference.contentType !== candidate.contentType) {
    return {
      equal: false,
      reason: `content-type ${reference.contentType || '(empty)'} !== ${candidate.contentType || '(empty)'}`,
    };
  }

  const referenceBody = stringifyNormalizedResponse(reference.body);
  const candidateBody = stringifyNormalizedResponse(candidate.body);

  if (referenceBody !== candidateBody) {
    return {
      equal: false,
      reason: 'body mismatch',
      diff: renderDiff(referenceBody, candidateBody),
    };
  }

  return { equal: true };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help) {
    printHelp();
    return;
  }

  const targets = resolveTargetDefinitions(args.targets);
  const fixturesFile = args.fixturesFile
    ? path.resolve(process.cwd(), args.fixturesFile)
    : fileURLToPath(DEFAULT_FIXTURES_FILE);
  const artifactsDir = args.artifactsDir ? path.resolve(process.cwd(), args.artifactsDir) : null;
  const endpoints = await readJsonFile(fixturesFile);

  if (!Array.isArray(endpoints) || endpoints.length === 0) {
    throw new Error('The fixtures file must contain at least one endpoint definition');
  }

  if (targets.length < 2) {
    console.log('At least two targets are required for a comparison run.');
    console.log('Provide them with `--target` or the `API_TARGETS` environment variable.');
    console.log(`Loaded ${endpoints.length} endpoint fixture(s) from ${fixturesFile}.`);
    process.exitCode = 2;
    return;
  }

  console.log(`Comparing ${endpoints.length} endpoint(s) across ${targets.length} target(s).`);
  console.log(`Fixtures: ${fixturesFile}`);

  const failures = [];

  for (const endpoint of endpoints) {
    const { endpointLabel, responses } = await fetchResponsesForEndpoint(
      endpoint,
      targets,
      args.timeoutMs,
      artifactsDir,
    );
    compareEndpointResponses(endpointLabel, responses, failures);
  }

  if (failures.length > 0) {
    console.log(`\nComparison failed for ${failures.length} endpoint/target pair(s).`);
    process.exitCode = 1;
    return;
  }

  console.log('\nAll endpoint responses matched across the configured targets.');
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});