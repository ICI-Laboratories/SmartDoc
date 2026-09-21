FROM rust:1.94-slim-bookworm AS builder
WORKDIR /app
COPY api_gateway/Cargo.toml api_gateway/Cargo.lock ./
COPY api_gateway/src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=builder /app/target/release/api_gateway .
USER 10001:10001
EXPOSE 8080
CMD ["./api_gateway"]
