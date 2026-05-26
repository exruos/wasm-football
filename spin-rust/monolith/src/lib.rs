use spin_sdk::http::{Params, Request, Response, Router};
use spin_sdk::http_component;

#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    players::register_routes(&mut router);
    teams::register_routes(&mut router);
    r#match::register_routes(&mut router);

    router.any("/*", |_: Request, _: Params| {
        Response::builder()
            .status(404)
            .header("Content-Type", "application/json")
            .body("{\"status\":404,\"error\":\"Not Found\"}".to_string())
            .build()
    });

    router.handle(req)
}
