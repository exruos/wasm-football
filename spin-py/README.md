# football spin-py

This project runs the same football API core in two runtimes:

- Spin Wasm component (`app.py` adapter)
- Container HTTP server (`container_server.py` adapter)

Both adapters reuse shared route/query logic from `football_core.py`.

## Spin Wasm

```bash
spin up --build
```

## Container Server

Build image:

```bash
docker build -t football-py-container .
```

Run image:

```bash
docker run --rm -p 3000:3000 -e DB_URL="postgres://postgres:postgres@host.docker.internal:5438/postgres" football-py-container
```

The container server listens on `http://localhost:3000`.
