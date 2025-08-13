mod database;

use axum::{
    body::{to_bytes, Body},
    extract::{Request, State},
    http::{self, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
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
use tracing::{error, info};

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
        .connect_timeout(std::time::Duration::from_secs(3))
        .timeout(std::time::Duration::from_secs(15))
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
        .layer(middleware::from_fn_with_state(
            state.clone(),
            user_validator,
        ))
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

    let user_header = req
        .headers()
        .get("X-User-ID")
        .and_then(|v| v.to_str().ok());

    match user_header {
        Some(username) if !username.is_empty() => {
            let first_seen = state
                .active_users
                .insert(username.to_string(), Instant::now())
                .is_none();

            if first_seen {
                if let Err(e) = database::find_or_create_user(&state.db, username).await {
                    error!(%username, error=%e, "Error de base de datos");
                    return Err(StatusCode::INTERNAL_SERVER_ERROR);
                }
            }

            Ok(next.run(req).await)
        }
        _ => {
            let body = "<h1>401 Unauthorized</h1><p>Header 'X-User-ID' es requerido.</p>";
            Ok((StatusCode::UNAUTHORIZED, Html(body)).into_response())
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

    let response_body = json!({
        "active_users_last_5_minutes": active_user_count,
        "total_tracked_users": state.active_users.len(),
    });

    (StatusCode::OK, Json(response_body))
}

async fn proxy_handler(client: Client, base_url: &str, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let method_ax = parts.method;
    let path = parts.uri.path();
    let query = parts.uri.query().map(|q| format!("?{q}")).unwrap_or_default();
    let target_url = format!("{}{}{}", base_url.trim_end_matches('/'), path, query);

    // 1) Método: http(1.x) -> reqwest/http(0.2)
    let method_req =
        reqwest::Method::from_bytes(method_ax.as_str().as_bytes()).unwrap_or(reqwest::Method::GET);

    // 2) Headers: http(1.x) -> reqwest/http(0.2)
    let headers_req = headers_to_reqwest(parts.headers.iter());

    // 3) Cuerpo (límite 2MB de ejemplo)
    let body_bytes = match to_bytes(body, 2 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => return (StatusCode::BAD_REQUEST, Html(format!("Cuerpo inválido: {e}"))).into_response(),
    };

    let reqwest_req = client
        .request(method_req, &target_url)
        .headers(headers_req)
        .body(body_bytes);

    match reqwest_req.send().await {
        Ok(resp) => {
            // 4) Status: reqwest/http(0.2) -> http(1.x)
            let status =
                http::StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);

            // 5) Headers: reqwest/http(0.2) -> http(1.x)
            let headers_ax = headers_from_reqwest(resp.headers().iter());

            let mut builder = Response::builder().status(status);
            if let Some(h) = builder.headers_mut() {
                *h = headers_ax;
            }
            builder
                .body(Body::from_stream(resp.bytes_stream()))
                .unwrap_or_else(|_| (StatusCode::INTERNAL_SERVER_ERROR, Html("Error creando respuesta")).into_response())
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Html(format!(
                "<h1>502 Bad Gateway</h1><p>Error al contactar el servicio interno: {e}</p>"
            )),
        )
            .into_response(),
    }
}

// --------- Helpers de headers / hop-by-hop ---------

fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailer"
            | "trailers"
            | "transfer-encoding"
            | "upgrade"
            | "host"
            | "content-length"
    )
}

/// Convierte `http(1.x)::HeaderMap` -> `reqwest/http(0.2)::HeaderMap` filtrando hop-by-hop
fn headers_to_reqwest<'a>(
    headers: impl Iterator<Item = (&'a http::HeaderName, &'a http::HeaderValue)>,
) -> reqwest::header::HeaderMap {
    let mut out = reqwest::header::HeaderMap::new();
    for (name, value) in headers {
        let n = name.as_str();
        if is_hop_by_hop(n) {
            continue;
        }
        if let (Ok(n2), Ok(v2)) = (
            reqwest::header::HeaderName::from_bytes(n.as_bytes()),
            reqwest::header::HeaderValue::from_bytes(value.as_bytes()),
        ) {
            out.append(n2, v2);
        }
    }
    out
}

/// Convierte `reqwest/http(0.2)::HeaderMap` -> `http(1.x)::HeaderMap` filtrando hop-by-hop
fn headers_from_reqwest<'a>(
    headers: impl Iterator<Item = (&'a reqwest::header::HeaderName, &'a reqwest::header::HeaderValue)>,
) -> http::HeaderMap {
    let mut out = http::HeaderMap::new();
    for (name, value) in headers {
        let n = name.as_str();
        if is_hop_by_hop(n) {
            continue;
        }
        if let (Ok(n2), Ok(v2)) = (
            http::HeaderName::from_bytes(n.as_bytes()),
            http::HeaderValue::from_bytes(value.as_bytes()),
        ) {
            out.append(n2, v2);
        }
    }
    out
}
