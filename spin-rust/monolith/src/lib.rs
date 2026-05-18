use spin_sdk::http::{Params, Request, Response, Router};
use spin_sdk::http_component;

#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    players::register_routes(&mut router);
    teams::register_routes(&mut router);
    r#match::register_routes(&mut router);

    router.get("/...", |_req: Request, _params: Params| {
        Response::builder()
            .status(404)
            .body("Not Found")
            .build()
    });

    router.handle(req)
}
