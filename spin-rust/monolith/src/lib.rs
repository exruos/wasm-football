use anyhow::Result;
use axum::Router;
use spin_sdk::http::{IntoResponse, Request};
use spin_sdk::http_service;
use tower::util::ServiceExt;

#[http_service]
async fn handle_request(req: Request) -> Result<impl IntoResponse> {
    let router = r#match::register_routes(teams::register_routes(players::register_routes(
        Router::new(),
    )));

    let response = router.oneshot(req).await.unwrap_or_else(|err| match err {});
    Ok(response)
}
