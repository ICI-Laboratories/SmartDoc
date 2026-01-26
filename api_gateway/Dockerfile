FROM rust:1.84-slim-bullseye as builder

WORKDIR /app

# Copy manifest and source
COPY api_gateway/Cargo.toml api_gateway/Cargo.lock ./
COPY api_gateway/src ./src

# Build
RUN cargo build --release

FROM debian:bullseye-slim
WORKDIR /app

# Copy binary
COPY --from=builder /app/target/release/api_gateway .

# Install dependencies if needed (sqlite3 is static in rust usually, but just in case)
# glibc is usually required.

# Expose port (default axum port usually 3000 or 8080, need to check main.rs)
EXPOSE 8080

CMD ["./api_gateway"]
