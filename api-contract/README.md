# Football API Contract Tests

This folder contains a standalone Node.js test harness that compares the HTTP output of multiple API implementations for the same request set.

It checks these endpoints:

- `/players/{id}` with `id=1`
- `/players/record/{id}` with `id=1`
- `/teams/{id}` with `id=1`
- `/teams/api-id/{id}` with `id=9930`
- `/match/{id}` with `id=1`
- `/match/team/{id}` with `id=1601`
- `/match/result-table?season=2015/2016&leagueName=Germany 1. Bundesliga`
- `/does-not-exist` to verify the 404 response contract

The comparator parses JSON responses, trims plain-text bodies, compares status code and content type, checks the calculated body byte length, and then compares the normalized body.

## Run

Use at least two base URLs when comparing implementations:

```powershell
cd api-contract
$env:API_TARGETS = "http://localhost:8088,http://localhost:8089"
npm run compare
```

Or pass targets explicitly:

```powershell
cd api-contract
npm run compare -- --target http://localhost:8088 --target http://localhost:8089
```

## Artifacts

To save raw responses for debugging, add an artifacts directory:

```powershell
cd api-contract
npm run compare -- --target http://localhost:8088 --target http://localhost:8089 --artifacts-dir .\artifacts\latest
```

Each endpoint/target pair is written as JSON under that directory.