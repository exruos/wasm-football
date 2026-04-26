from contextlib import closing
import os
import re
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
import pg8000.dbapi

from football_core import RouteResult, handle_get_request, not_found_result

DB_URL_DEFAULT = "postgres://postgres:postgres@localhost:5438/postgres"

app = FastAPI(title="football-py-container")


class Pg8000DbClient:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url

    def query_rows(self, sql: str, params: list[object]) -> list[dict[str, object]]:
        converted_sql = re.sub(r"\$\d+", "%s", sql)
        parsed = urlparse(self.db_url)

        with closing(
            pg8000.dbapi.connect(
                user=parsed.username or "postgres",
                password=parsed.password or "postgres",
                host=parsed.hostname or "localhost",
                port=parsed.port or 5432,
                database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            )
        ) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(converted_sql, tuple(params))
                rows = cursor.fetchall()
                columns = [str(desc[0]) for desc in cursor.description or []]

        return [dict(zip(columns, row)) for row in rows]


def to_fastapi_response(result: RouteResult) -> Response:
    if result.content_type == "application/json":
        return JSONResponse(content=result.payload, status_code=result.status)

    if result.content_type == "text/plain":
        return PlainTextResponse(content=str(result.payload or ""), status_code=result.status)

    return Response(status_code=result.status)


@app.get("/{full_path:path}")
async def handle_all(full_path: str, request: Request) -> Response:
    path = "/" + full_path
    query = parse_qs(request.url.query)

    db_url = os.getenv("DB_URL", DB_URL_DEFAULT)
    db = Pg8000DbClient(db_url)
    result = handle_get_request(db, path, query)

    return to_fastapi_response(result)
