FROM rust:1.96 AS chef
RUN cargo install --locked cargo-chef
WORKDIR /app

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
# Build dependencies - this is the caching Docker layer!
RUN cargo chef cook --release --recipe-path recipe.json
# Build application
COPY . .
RUN cargo build --release -p rust-football-api

FROM debian:trixie-slim AS runtime
WORKDIR /app
COPY --from=builder /app/target/release/rust-football-api /usr/local/bin/rust-football-api

ENV RUST_LOG=info
EXPOSE 8080

CMD ["/usr/local/bin/rust-football-api"]
