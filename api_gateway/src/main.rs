// api_gateway/src/main.rs

// Importamos los módulos y crates necesarios
mod database;

use axum::{
    body::{Body, Bytes},
    extract::{Request, State},
    http::{header, HeaderMap, HeaderValue, Method, StatusCode, Uri},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::any,
    Router,
};
use chrono::{Duration, Utc};
use dashmap::DashMap;
use reqwest::Client;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

/// El estado compartido de nuestra aplicación, accesible desde todos los handlers.
#[derive(Clone)]
struct AppState {
    db: database::Db,
    http_client: Client,
    // (Username, Timestamp) -> Usamos DashMap para acceso concurrente seguro y rápido.
    active_users: Arc<DashMap<String, Instant>>,
    // URLs de los servicios internos
    doc_processor_url: String,
    llm_service_url: String,
}

/// Punto de entrada principal de la aplicación.
#[tokio::main]
async fn main() {
    // Configuración (idealmente, esto vendría de variables de entorno)
    let db_path = Path::new("smartdoc_users.db");
    let doc_processor_url = "http://127.0.0.1:8002".to_string();
    let llm_service_url = "http://127.0.0.1:8001".to_string();

    // Inicializa la base de datos
    let db = database::init(db_path).expect("No se pudo inicializar la base de datos");

    // Crea el estado compartido de la aplicación
    let state = AppState {
        db,
        http_client: Client::new(),
        active_users: Arc::new(DashMap::new()),
        doc_processor_url,
        llm_service_url,
    };

    // Define el router de la aplicación con sus rutas y middlewares
    let app = Router::new()
        .route("/metrics", any(metrics_handler)) // Endpoint de métricas, no necesita validación de usuario
        .route("/process_document/*path", any(process_document_handler))
        .fallback(llm_handler) // Cualquier otra ruta va al servicio de LLM
        .layer(middleware::from_fn_with_state(
            state.clone(),
            user_validator,
        ))
        .with_state(state);

    // Dirección y puerto donde correrá el servidor
    let addr = SocketAddr::from(([127, 0, 0, 1], 8000));
    println!("API Gateway escuchando en http://{}", addr);

    // Inicia el servidor
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

/// Middleware para validar al usuario en cada petición.
async fn user_validator(
    State(state): State<AppState>,
    mut req: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    // Excluimos el endpoint de métricas de la validación
    if req.uri().path() == "/metrics" {
        return Ok(next.run(req).await);
    }
    
    // Busca el header 'X-User-ID'
    let user_header = req
        .headers()
        .get("X-User-ID")
        .and_then(|value| value.to_str().ok());

    match user_header {
        Some(username) if !username.is_empty() => {
            // Si el header existe y no está vacío, valida/crea el usuario en la DB
            if let Err(e) = database::find_or_create_user(&state.db, username).await {
                eprintln!("Error de base de datos: {}", e);
                return Err(StatusCode::INTERNAL_SERVER_ERROR);
            }
            
            // Actualiza el timestamp del usuario para las métricas de actividad
            state.active_users.insert(username.to_string(), Instant::now());
            
            // Pasa la petición al siguiente handler
            Ok(next.run(req).await)
        }
        _ => {
            // Si el header no existe o está vacío, rechaza la petición
            let body = "<h1>401 Unauthorized</h1><p>Header 'X-User-ID' es requerido.</p>";
            let response = Html(body)
                .into_response()
                .with_status(StatusCode::UNAUTHORIZED);
            Ok(response)
        }
    }
}

/// Handler para reenviar peticiones al Document Processor Service.
async fn process_document_handler(
    State(state): State<AppState>,
    req: Request,
) -> Response {
    proxy_handler(state.http_client, &state.doc_processor_url, req).await
}

/// Handler para reenviar todas las demás peticiones al LLM Service.
async fn llm_handler(
    State(state): State<AppState>,
    req: Request,
) -> Response {
    proxy_handler(state.http_client, &state.llm_service_url, req).await
}

/// Handler para el endpoint de métricas.
async fn metrics_handler(State(state): State<AppState>) -> impl IntoResponse {
    // Filtra el mapa para contar solo los usuarios activos en los últimos 5 minutos
    let five_minutes_ago = Instant::now() - std::time::Duration::from_secs(300);
    
    let active_user_count = state.active_users
        .iter()
        .filter(|entry| *entry.value() > five_minutes_ago)
        .count();
        
    let response_body = serde_json::json!({
        "active_users_last_5_minutes": active_user_count,
        "total_tracked_users": state.active_users.len(),
    });

    (StatusCode::OK, axum::Json(response_body))
}


/// Función genérica de proxy para reenviar peticiones.
async fn proxy_handler(
    client: Client,
    base_url: &str,
    req: Request,
) -> Response {
    // Construye la URL del servicio de destino
    let path = req.uri().path();
    let query = req.uri().query().map_or("".to_string(), |q| format!("?{}", q));
    let target_url = format!("{}{}{}", base_url, path, query);

    // Reenvía el método, headers y cuerpo de la petición original
    let res = client
        .request(req.method().clone(), &target_url)
        .headers(req.headers().clone())
        .body(req.into_body())
        .send()
        .await;

    // Procesa la respuesta del servicio de destino
    match res {
        Ok(response) => {
            // Reenvía el status y headers de la respuesta al cliente original
            let mut builder = Response::builder().status(response.status());
            *builder.headers_mut().unwrap() = response.headers().clone();
            builder.body(Body::from_stream(response.bytes_stream())).unwrap()
        }
        Err(e) => {
            // Si hay un error de conexión, devuelve un 502 Bad Gateway
            let body = format!("<h1>502 Bad Gateway</h1><p>Error al contactar el servicio interno: {}</p>", e);
            Html(body)
                .into_response()
                .with_status(StatusCode::BAD_GATEWAY)
        }
    }
}