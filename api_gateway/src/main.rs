// api_gateway/src/main.rs

mod database;

use axum::{
    body::{to_bytes, Body},
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::{any, get},
    Router, Json,
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

/// Estado compartido de la app
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
    // Logging estructurado
    tracing_subscriber::fmt::init();

    // Configuración (idealmente via variables de entorno)
    let db_path = Path::new("smartdoc_users.db");
    let doc_processor_url = "http://127.0.0.1:8002".to_string();
    let llm_service_url = "http://127.0.0.1:8001".to_string();

    // Cliente HTTP con timeouts razonables
    let http_client = Client::builder()
        .connect_timeout(std::time::Duration::from_secs(3))
        .timeout(std::time::Duration::from_secs(15))
        .pool_idle_timeout(std::time::Duration::from_secs(30))
        .build()
        .expect("no se pudo crear reqwest::Client");

    // Inicializa DB (async, usando tokio_rusqlite en database.rs)
    let db = database::init(db_path)
        .await
        .expect("No se pudo inicializar la base de datos");

    // Estado compartido
    let state = AppState {
        db,
        http_client,
        active_users: Arc::new(DashMap::new()),
        doc_processor_url,
        llm_service_url,
    };

    // Tarea de limpieza periódica de usuarios inactivos
    {
        let active = state.active_users.clone();
        tokio::spawn(async move {
            let ttl = std::time::Duration::from_secs(300); // 5 min
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                let min_instant = Instant::now() - ttl;
                active.retain(|_, t| *t > min_instant);
            }
        });
    }

    // Router
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

    // Servidor
    let addr = SocketAddr::from(([127, 0, 0, 1], 8000));
    info!("API Gateway escuchando en http://{addr}");
    let listener = TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

/// Middleware: valida usuario por header `X-User-ID`
/// - Excluye `/metrics`
/// - Registra en DB sólo la primera vez que vemos al usuario en la ventana reciente
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
            // Inserta en el mapa y sólo toca DB si es la primera vez reciente
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

/// Proxy a Document Processor
async fn process_document_handler(State(state): State<AppState>, req: Request) -> Response {
    proxy_handler(state.http_client.clone(), &state.doc_processor_url, req).await
}

/// Proxy a LLM Service (fallback)
async fn llm_handler(State(state): State<AppState>, req: Request) -> Response {
    proxy_handler(state.http_client.clone(), &state.llm_service_url, req).await
}

/// Métricas JSON sencillas
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

/// Proxy genérico con filtrado de headers hop-by-hop y streaming de respuesta
async fn proxy_handler(client: Client, base_url: &str, req: Request) -> Response {
    // Desensambla request
    let (parts, body) = req.into_parts();
    let method = parts.method;
    let path = parts.uri.path();
    let query = parts.uri.query().map(|q| format!("?{q}")).unwrap_or_default();

    let target_url = format!(
        "{}{}{}",
        base_url.trim_end_matches('/'),
        path,
        query
    );

    // Lee el cuerpo (límite 2MB como ejemplo)
    let body_bytes = match to_bytes(body, 2 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Html(format!("Cuerpo inválido: {e}")),
            )
                .into_response()
        }
    };

    // Construye request hacia el backend
    let reqwest_req = client
        .request(method, &target_url)
        .headers(filtered_headers(&parts.headers))
        .body(body_bytes);

    // Envía y procesa respuesta
    match reqwest_req.send().await {
        Ok(resp) => {
            let status = resp.status();
            let mut builder = http::Response::builder().status(status);
            if let Some(h) = builder.headers_mut() {
                *h = filtered_headers(resp.headers());
            }
            // Stream back to client
            builder
                .body(Body::from_stream(resp.bytes_stream()))
                .unwrap_or_else(|_| {
                    (StatusCode::INTERNAL_SERVER_ERROR, Html("Error creando respuesta"))
                        .into_response()
                })
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

/// Filtra headers hop-by-hop + algunos sensibles que no deben reenviarse
fn filtered_headers(src: &HeaderMap) -> HeaderMap {
    const HOP: &[&str] = &[
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ];

    let mut dst = HeaderMap::new();
    for (name, value) in src.iter() {
        let n = name.as_str();
        if n.eq_ignore_ascii_case("host")
            || n.eq_ignore_ascii_case("content-length")
            || HOP.iter().any(|h| n.eq_ignore_ascii_case(h))
        {
            continue;
        }
        dst.append(name.clone(), value.clone());
    }
    dst
}
