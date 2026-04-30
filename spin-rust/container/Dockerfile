FROM rust:1.95-slim AS builder
WORKDIR /app

COPY . /app
RUN cargo build --release -p rust-football-api

FROM debian:bookworm-slim AS runtime
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/rust-football-api /usr/local/bin/rust-football-api

ENV RUST_LOG=info
EXPOSE 8088

CMD ["/usr/local/bin/rust-football-api"]
