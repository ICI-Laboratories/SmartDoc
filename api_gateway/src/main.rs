// api_gateway/src/main.rs
mod database;

use axum::{
    body::{to_bytes, Body},
    extract::{Request, State},
    http::{header, HeaderMap, HeaderName, HeaderValue, Method, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{any, get},
    Json, Router,
};
use dashmap::DashMap;
use reqwest::Client;
use serde_json::json;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;
use tokio::net::TcpListener;
use tower_http::{compression::CompressionLayer, trace::TraceLayer};
use tracing::{error, info, warn};

#[derive(Clone)]
struct AppState {
    db: database::Db,
    http_client: Client,
    active_users: Arc<DashMap<String, Instant>>,
    doc_processor_url: String,
    llm_service_url: String,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let db_path = Path::new("smartdoc_users.db");
    let doc_processor_url = "http://127.0.0.1:8002".to_string();
    let llm_service_url = "http://127.0.0.1:8001".to_string();

    let http_client = Client::builder()
        .connect_timeout(std::time::Duration::from_secs(5))
        .timeout(std::time::Duration::from_secs(90))
        .pool_idle_timeout(std::time::Duration::from_secs(30))
        .build()
        .expect("no se pudo crear reqwest::Client");

    let db = database::init(db_path)
        .await
        .expect("No se pudo inicializar la base de datos");

    let state = AppState {
        db,
        http_client,
        active_users: Arc::new(DashMap::new()),
        doc_processor_url,
        llm_service_url,
    };

    // GC simple de usuarios activos (TTL 5 min)
    {
        let active = state.active_users.clone();
        tokio::spawn(async move {
            let ttl = std::time::Duration::from_secs(300);
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                let min_instant = Instant::now() - ttl;
                active.retain(|_, t| *t > min_instant);
            }
        });
    }

    let app = Router::new()
        .route("/metrics", get(metrics_handler))
        .route("/process_document/*path", any(process_document_handler))
        .fallback(llm_handler)
        .layer(middleware::from_fn_with_state(state.clone(), user_validator))
        .layer(CompressionLayer::new())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], 8000));
    info!("API Gateway escuchando en http://{addr}");
    let listener = TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn user_validator(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    if req.uri().path() == "/metrics" {
        return Ok(next.run(req).await);
    }

    let user_header = req.headers().get("X-User-ID").and_then(|v| v.to_str().ok());

    match user_header {
        Some(username) if !username.is_empty() => {
            if state
                .active_users
                .insert(username.to_string(), Instant::now())
                .is_none()
            {
                if let Err(e) = database::find_or_create_user(&state.db, username).await {
                    error!(%username, error=%e, "Error de base de datos");
                    return Err(StatusCode::INTERNAL_SERVER_ERROR);
                }
            }
            Ok(next.run(req).await)
        }
        _ => {
            let body = Json(json!({
                "error": "Header 'X-User-ID' es requerido y no puede estar vacío.",
                "status": 401
            }));
            Ok((StatusCode::UNAUTHORIZED, body).into_response())
        }
    }
}

async fn process_document_handler(State(state): State<AppState>, req: Request) -> Response {
    proxy_handler(state.http_client.clone(), &state.doc_processor_url, req).await
}

async fn llm_handler(State(state): State<AppState>, req: Request) -> Response {
    proxy_handler(state.http_client.clone(), &state.llm_service_url, req).await
}

async fn metrics_handler(State(state): State<AppState>) -> impl IntoResponse {
    let five_minutes_ago = Instant::now() - std::time::Duration::from_secs(300);
    let active_user_count = state
        .active_users
        .iter()
        .filter(|entry| *entry.value() > five_minutes_ago)
        .count();

    (
        StatusCode::OK,
        Json(json!({
            "active_users_last_5_minutes": active_user_count,
            "total_tracked_users": state.active_users.len(),
        })),
    )
}

// -------------------- Proxy con conversiones explícitas --------------------

async fn proxy_handler(client: Client, base_url: &str, req: Request) -> Response {
    let (parts, body) = req.into_parts();

    let path = parts.uri.path();
    let query = parts.uri.query().map(|q| format!("?{q}")).unwrap_or_default();
    let target_url = format!("{}{}{}", base_url.trim_end_matches('/'), path, query);

    // Limite de 50MB
    let body_bytes = match to_bytes(body, 50 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            warn!("Cuerpo de la petición inválido o demasiado grande: {e}");
            let body = Json(json!({
                "error": "Cuerpo de la petición inválido o demasiado grande.",
                "detail": e.to_string(),
                "status": 400
            }));
            return (StatusCode::BAD_REQUEST, body).into_response();
        }
    };

    // --- Conversión de tipos entre http v1 (axum) y http v0.2 (reqwest 0.11) ---
    let method = method_to_reqwest(&parts.method);
    let req_headers = headers_axum_to_reqwest(&parts.headers);

    let reqwest_req = client
        .request(method, &target_url)
        .headers(req_headers)
        .body(body_bytes);

    match reqwest_req.send().await {
        Ok(resp) => {
            // Status: reqwest::StatusCode (http 0.2) -> axum::http::StatusCode (http 1)
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::BAD_GATEWAY);

            let mut builder = Response::builder().status(status);

            // Copiar headers respuesta: reqwest -> axum (filtrando hop-by-hop)
            if let Some(hm) = builder.headers_mut() {
                for (name, value) in resp.headers().iter() {
                    if !is_hop_by_hop(name.as_str()) {
                        if let (Ok(n), Ok(v)) = (
                            HeaderName::from_bytes(name.as_str().as_bytes()),
                            HeaderValue::from_bytes(value.as_bytes()),
                        ) {
                            hm.insert(n, v);
                        }
                    }
                }
            }

            // Streaming de cuerpo
            builder
                .body(Body::from_stream(resp.bytes_stream()))
                .unwrap_or_else(|_| {
                    (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "Error creando respuesta",
                    )
                        .into_response()
                })
        }
        Err(e) => {
            error!("Error al contactar el servicio interno en {target_url}: {e}");
            let error_body = Json(json!({
                "error": "No se pudo contactar con el servicio interno.",
                "detail": e.to_string(),
                "status": 502
            }));
            (
                StatusCode::BAD_GATEWAY,
                [(header::CONTENT_TYPE, "application/json")],
                error_body,
            )
                .into_response()
        }
    }
}

fn method_to_reqwest(method: &Method) -> reqwest::Method {
    // Evita TryFrom entre crates `http` distintos
    reqwest::Method::from_bytes(method.as_str().as_bytes()).unwrap_or(reqwest::Method::GET)
}

fn headers_axum_to_reqwest(headers: &HeaderMap<HeaderValue>) -> reqwest::header::HeaderMap {
    let mut out = reqwest::header::HeaderMap::new();
    for (name, value) in headers.iter() {
        if !is_hop_by_hop(name.as_str()) {
            if let (Ok(n), Ok(v)) = (
                reqwest::header::HeaderName::from_bytes(name.as_str().as_bytes()),
                reqwest::header::HeaderValue::from_bytes(value.as_bytes()),
            ) {
                out.insert(n, v);
            }
        }
    }
    out
}

fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailers"
            | "transfer-encoding"
            | "upgrade"
            | "host"
    )
}
