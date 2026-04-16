use spin_sdk::http::{Request, Response, Router};
use spin_sdk::http_component;

#[http_component]
fn handle_request(req: Request) -> Response {
    let mut router = Router::new();

    players::register_routes(&mut router);
    teams::register_routes(&mut router);
    matches::register_routes(&mut router);

    router.handle(req)
}
