FROM rust:1.88-slim-bookworm AS builder

WORKDIR /app

# Install pkg-config and OpenSSL dev dependencies
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

# Copy manifest and source
COPY api_gateway/Cargo.toml ./
COPY api_gateway/src ./src

# Build
RUN cargo build --release

FROM debian:bookworm-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy binary
COPY --from=builder /app/target/release/api_gateway .

# Install dependencies if needed (sqlite3 is static in rust usually, but just in case)
# glibc is usually required.

# Expose port (default axum port usually 3000 or 8080, need to check main.rs)
EXPOSE 8080

CMD ["./api_gateway"]
