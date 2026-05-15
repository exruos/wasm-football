import json
from urllib.parse import parse_qs, urlsplit

from spin_sdk.http import Handler, Request, Response
from spin_sdk import variables
from spin_sdk.wit.imports import spin_postgres_postgres_4_2_0 as pg

from football_core import RouteResult, handle_get_request, not_found_result

DB_URL_DEFAULT = "postgres://postgres:postgres@localhost:5438/postgres"


def db_value_to_python(value: object) -> object:
    if isinstance(value, pg.DbValue_DbNull):
        return None
    if hasattr(value, "value"):
        return value.value
    return value


def row_to_dict(columns: list[pg.Column], row: list[object]) -> dict[str, object]:
    return {column.name: db_value_to_python(cell) for column, cell in zip(columns, row)}


def to_param(value: object) -> object:
    if value is None:
        return pg.ParameterValue_DbNull()
    if isinstance(value, bool):
        return pg.ParameterValue_Boolean(value)
    if isinstance(value, int):
        return pg.ParameterValue_Int32(value)
    return pg.ParameterValue_Str(str(value))


class SpinDbClient:
    def __init__(self, connection: pg.Connection) -> None:
        self.connection = connection

    def query_rows(self, sql: str, params: list[object]) -> list[dict[str, object]]:
        row_set = self.connection.query(sql, [to_param(param) for param in params])
        return [row_to_dict(row_set.columns, row) for row in row_set.rows]


def to_spin_response(result: RouteResult) -> Response:
    if result.content_type == "application/json":
        return Response(
            result.status,
            {"content-type": "application/json"},
            json.dumps(result.payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        )

    if result.content_type == "text/plain":
        return Response(result.status, {"content-type": "text/plain"}, str(result.payload or "").encode("utf-8"))

    return Response(result.status, {}, None)


async def get_connection() -> pg.Connection:
    try:
        db_url = await variables.get("db_url")
    except Exception:
        db_url = DB_URL_DEFAULT
    return pg.Connection.open(db_url or DB_URL_DEFAULT)


class HttpHandler(Handler):
    async def handle_request(self, request: Request) -> Response:
        if request.method != "GET":
            return to_spin_response(not_found_result())

        parsed_uri = urlsplit(request.uri)
        path = parsed_uri.path or "/"
        query = parse_qs(parsed_uri.query)

        connection = await get_connection()
        db = SpinDbClient(connection)
        result = handle_get_request(db, path, query)

        return to_spin_response(result)
