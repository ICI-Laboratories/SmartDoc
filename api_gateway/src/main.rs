use anyhow::{anyhow, Context, Result};
use axum::{
    body::{to_bytes, Body},
    extract::{Extension, Query, Request, State},
    http::{header, HeaderMap, HeaderName, HeaderValue, Method, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{any, get, post},
    Json, Router,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use dashmap::DashMap;
use dotenv::dotenv;
use rand::{rngs::OsRng, RngCore};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    env,
    net::SocketAddr,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::{net::TcpListener, sync::Mutex};
use tower_http::{
    compression::CompressionLayer,
    cors::{AllowOrigin, CorsLayer},
};
use tracing::{error, info, warn};
use url::Url;
use uuid::Uuid;

const CLIENT_ID: &str = "smartdoc-web";
const RESOURCE_AUDIENCE: &str = "smartdoc";
const MAX_PENDING_OAUTH_TRANSACTIONS: usize = 10_000;

#[derive(Clone)]
struct Config {
    anonymous: bool,
    login_enabled: bool,
    ephemeral_hours: u64,
    auth_portal_authorize_url: Url,
    identity_url: Url,
    callback_url: Url,
    frontend_url: Url,
    frontend_origin: String,
    session_cookie_name: String,
    csrf_cookie_name: String,
    oauth_state_cookie_name: String,
    oauth_verifier_cookie_name: String,
    cookie_domain: Option<String>,
    cookie_secure: bool,
    oauth_transaction_ttl: Duration,
    doc_processor_url: String,
    llm_service_url: String,
    library_url: String,
    max_body_bytes: usize,
    listen_addr: SocketAddr,
}

impl Config {
    fn from_env() -> Result<Self> {
        let portal_base = parse_base_url("SMARTDOC_AUTH_PORTAL_URL", "http://127.0.0.1:3000")?;
        let identity_url = parse_base_url("SMARTDOC_IDENTITY_URL", "http://127.0.0.1:8001")?;
        let callback_url = parse_exact_url(
            "SMARTDOC_AUTH_CALLBACK_URL",
            "http://127.0.0.1:8043/auth/callback",
        )?;
        let frontend_url = parse_exact_url("SMARTDOC_FRONTEND_URL", "http://127.0.0.1:8501/")?;
        enforce_transport_security("SMARTDOC_AUTH_CALLBACK_URL", &callback_url)?;
        enforce_transport_security("SMARTDOC_FRONTEND_URL", &frontend_url)?;
        if callback_url.path() != "/auth/callback" {
            return Err(anyhow!(
                "SMARTDOC_AUTH_CALLBACK_URL debe terminar exactamente en /auth/callback"
            ));
        }

        let auth_portal_authorize_url = portal_base
            .join("authorize")
            .context("SMARTDOC_AUTH_PORTAL_URL no permite construir /authorize")?;
        enforce_transport_security("SMARTDOC_AUTH_PORTAL_URL", &portal_base)?;
        enforce_transport_security("SMARTDOC_IDENTITY_URL", &identity_url)?;
        let frontend_origin = url_origin(&frontend_url)?;
        let cookie_secure = optional_bool("SMARTDOC_COOKIE_SECURE")?
            .unwrap_or_else(|| callback_url.scheme() == "https");
        if callback_url.scheme() == "https" && !cookie_secure {
            return Err(anyhow!(
                "SMARTDOC_COOKIE_SECURE no puede desactivarse con callback HTTPS"
            ));
        }

        let cookie_domain = env::var("SMARTDOC_COOKIE_DOMAIN")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());
        if let Some(domain) = cookie_domain.as_deref() {
            if domain.contains([';', ' ', '\t', '\r', '\n']) {
                return Err(anyhow!("SMARTDOC_COOKIE_DOMAIN no es valido"));
            }
        }
        validate_cookie_scope(&callback_url, &frontend_url, cookie_domain.as_deref())?;

        let listen_addr = env::var("SMARTDOC_GATEWAY_LISTEN")
            .unwrap_or_else(|_| "127.0.0.1:8043".to_string())
            .parse()
            .context("SMARTDOC_GATEWAY_LISTEN no es una direccion socket valida")?;
        Ok(Self {
            anonymous: optional_bool("SARA_ANONYMOUS")?.unwrap_or(true),
            login_enabled: optional_bool("SARA_LOGIN_ENABLED")?.unwrap_or(false),
            ephemeral_hours: env_u64("SARA_EPHEMERAL_HOURS",24,1,168)?,
            auth_portal_authorize_url,
            identity_url,
            callback_url,
            frontend_url,
            frontend_origin,
            session_cookie_name: safe_cookie_name(
                "SMARTDOC_SESSION_COOKIE_NAME",
                "smartdoc_session",
            )?,
            csrf_cookie_name: safe_cookie_name("SMARTDOC_CSRF_COOKIE_NAME", "smartdoc_csrf")?,
            oauth_state_cookie_name: "smartdoc_oauth_state".to_string(),
            oauth_verifier_cookie_name: "smartdoc_oauth_verifier".to_string(),
            cookie_domain,
            cookie_secure,
            oauth_transaction_ttl: Duration::from_secs(env_u64(
                "SMARTDOC_OAUTH_TRANSACTION_TTL_SECONDS",
                300,
                60,
                600,
            )?),
            doc_processor_url: env::var("SMARTREVIEW_DOC_PROCESSOR_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8045".to_string()),
            llm_service_url: env::var("SMARTREVIEW_LLM_SERVICE_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8044".to_string()),
            library_url: env::var("SARA_LIBRARY_URL").unwrap_or_else(|_| "http://127.0.0.1:8046".to_string()),
            max_body_bytes: env_u64(
                "SMARTREVIEW_MAX_BODY_BYTES",
                100 * 1024 * 1024,
                1024,
                1024 * 1024 * 1024,
            )? as usize,
            listen_addr,
        })
    }
}

#[derive(Clone)]
struct CentralSession {
    access_token: String,
    refresh_token: String,
    access_expires_at: Instant,
    refresh_expires_at: Instant,
}

#[derive(Clone)]
struct OAuthTransaction {
    verifier_hash: String,
    expires_at: Instant,
}

#[derive(Clone)]
struct AppState {
    http_client: Client,
    sessions: Arc<DashMap<String, CentralSession>>,
    oauth_transactions: Arc<DashMap<String, OAuthTransaction>>,
    refresh_locks: Arc<DashMap<String, Arc<Mutex<()>>>>,
    active_users: Arc<DashMap<String, Instant>>,
    config: Arc<Config>,
}

#[derive(Clone, Debug, Serialize)]
struct Principal {
    subject: String,
}

#[derive(Debug, Deserialize)]
struct CentralPrincipal {
    id: String,
    is_active: bool,
}

#[derive(Debug, Deserialize)]
struct CentralTokenResponse {
    access_token: String,
    refresh_token: String,
    token_type: String,
    expires_in: u64,
    refresh_expires_in: u64,
    client_id: String,
}

#[derive(Debug, Deserialize)]
struct CallbackQuery {
    code: String,
    state: String,
}

#[derive(Debug)]
enum SessionError {
    Unauthorized,
    IdentityUnavailable,
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::fmt::init();
    let config = Arc::new(Config::from_env()?);
    let request_timeout = env_u64("SMARTREVIEW_GATEWAY_TIMEOUT", 300, 5, 3600)?;
    let http_client = Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(request_timeout))
        .pool_idle_timeout(Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .context("no se pudo crear el cliente HTTP")?;
    let state = AppState {
        http_client,
        sessions: Arc::new(DashMap::new()),
        oauth_transactions: Arc::new(DashMap::new()),
        refresh_locks: Arc::new(DashMap::new()),
        active_users: Arc::new(DashMap::new()),
        config: config.clone(),
    };
    spawn_session_cleanup(state.clone());

    let app = build_app(state)?;

    info!(
        address = %config.listen_addr,
        client_id = CLIENT_ID,
        audience = RESOURCE_AUDIENCE,
        anonymous = config.anonymous,
        "SARA DocReader gateway listo"
    );
    let listener = TcpListener::bind(config.listen_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

fn build_app(state: AppState) -> Result<Router> {
    if state.config.anonymous { return build_anonymous_app(state); }
    let protected = Router::new()
        .route("/api/*path", any(library_handler))
        .route("/process_document/", post(process_document_handler))
        .fallback(llm_handler);
    let allowed_origin = HeaderValue::from_str(&state.config.frontend_origin)
        .context("origen del frontend no valido")?;
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::exact(allowed_origin))
        .allow_credentials(true)
        .allow_methods([Method::GET, Method::POST])
        .allow_headers([
            header::CONTENT_TYPE,
            HeaderName::from_static("x-csrf-token"),
        ]);
    Ok(Router::new()
        .route("/health", get(health_handler))
        .route("/metrics", get(metrics_handler))
        .route("/auth/login", get(auth_login_handler))
        .route("/auth/callback", get(auth_callback_handler))
        .route("/auth/logout", post(auth_logout_handler))
        .route("/session/me", get(session_me_handler))
        .route("/session/refresh", post(session_refresh_handler))
        .merge(protected)
        .layer(middleware::from_fn_with_state(
            state.clone(),
            require_central_session,
        ))
        .layer(CompressionLayer::new())
        .layer(cors)
        .with_state(state))
}

const VISITOR_COOKIE: &str = "sara_visitor";

#[derive(Deserialize)]
struct AnonymousSession {
    subject: String,
    token: Option<String>,
    max_age: u64,
    #[serde(default)]
    ephemeral: bool,
}

fn build_anonymous_app(state: AppState) -> Result<Router> {
    let protected = Router::new()
        .route("/api/*path", any(library_handler))
        .route_layer(middleware::from_fn_with_state(state.clone(), require_anonymous_session));
    // Only the new catalog is reachable anonymously. Legacy identity namespaces
    // and internal session/analytics administration routes are never exposed.
    Ok(Router::new()
        .route("/health", get(health_handler))
        .route("/session/me", get(access_session_handler))
        .route("/session/anonymous", post(anonymous_session_handler))
        .route("/session/end", post(end_access_handler))
        .route("/auth/login", get(optional_login_handler))
        .route("/auth/callback", get(optional_callback_handler))
        .merge(protected)
        .layer(CompressionLayer::new())
        .with_state(state))
}

async fn resolve_anonymous(state: &AppState, headers: &HeaderMap, create: bool)
    -> std::result::Result<AnonymousSession, StatusCode> {
    let token = cookie_value(headers, VISITOR_COOKIE);
    if !create && token.is_none() { return Err(StatusCode::UNAUTHORIZED); }
    let response = state.http_client
        .post(format!("{}/internal/anonymous/session", state.config.library_url.trim_end_matches('/')))
        .timeout(Duration::from_secs(3))
        .json(&json!({"token":token,"create":create}))
        .send().await.map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err(StatusCode::UNAUTHORIZED);
    }
    if !response.status().is_success() { return Err(StatusCode::SERVICE_UNAVAILABLE); }
    let session: AnonymousSession = response.json().await.map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    if Uuid::parse_str(&session.subject).map(|id| id.to_string() != session.subject).unwrap_or(true)
        || session.max_age == 0 || session.max_age > 180*24*3600 {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    if session.token.as_ref().is_some_and(|value| value.len() < 32 || value.len() > 128
        || !value.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')) {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    Ok(session)
}

fn anonymous_error(status: StatusCode) -> Response {
    no_store_json(status,json!({"error": if status == StatusCode::UNAUTHORIZED {
        "Vuelve a abrir la biblioteca para iniciar una sesión anónima."
    } else { "No se pudo abrir la biblioteca. Intenta nuevamente." }}))
}

async fn anonymous_session_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if validate_mutation_request(&state.config,&headers).is_err()
        || headers.get("sec-fetch-site").and_then(|v|v.to_str().ok()) == Some("cross-site")
        || headers.get(header::ORIGIN).and_then(|v|v.to_str().ok())
            .is_some_and(|v| v != state.config.frontend_origin) {
        return anonymous_error(StatusCode::FORBIDDEN);
    }
    match resolve_anonymous(&state,&headers,true).await {
        Ok(session) => {
            let mut response = no_store_json(StatusCode::OK,json!({"anonymous":true,"mode":if session.ephemeral {"ephemeral"} else {"anonymous"},"expires_in":session.max_age,"login_available":state.config.login_enabled}));
            let mut cookie_config = (*state.config).clone();
            cookie_config.cookie_domain = None;
            if let Some(token) = session.token {
                append_cookie(&mut response,&build_cookie(VISITOR_COOKIE,&token,"/",session.max_age,true,&cookie_config));
            }
            if cookie_value(&headers,&state.config.csrf_cookie_name).is_none() {
                append_cookie(&mut response,&build_cookie(&state.config.csrf_cookie_name,&random_token(32),"/",session.max_age,false,&cookie_config));
            }
            append_cookie(&mut response,&delete_cookie(&state.config.session_cookie_name,"/",&state.config));
            response
        }
        Err(status) => anonymous_error(status),
    }
}

async fn optional_login_handler(State(state): State<AppState>) -> Response {
    if !state.config.login_enabled { return no_store_json(StatusCode::SERVICE_UNAVAILABLE,json!({"error":"El portal de cuentas aún no está configurado."})); }
    auth_login_handler(State(state)).await
}

async fn optional_callback_handler(State(state): State<AppState>, headers: HeaderMap, query: axum::extract::Query<CallbackQuery>) -> Response {
    if !state.config.login_enabled { return StatusCode::NOT_FOUND.into_response(); }
    auth_callback_handler(State(state),query,headers).await
}

async fn access_session_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if headers.get("sec-fetch-site").and_then(|v|v.to_str().ok()) == Some("cross-site")
        || headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).is_some_and(|v|v != state.config.frontend_origin) {
        return anonymous_error(StatusCode::FORBIDDEN);
    }
    let payload = if state.config.login_enabled && cookie_value(&headers,&state.config.session_cookie_name).is_some() {
        match authenticate_request(&state,&headers).await {
            Ok(_) => json!({"mode":"account","anonymous":false,"login_available":true}),
            Err(SessionError::Unauthorized) => json!({"mode":"choose","ephemeral_hours":state.config.ephemeral_hours,"login_available":true}),
            Err(error) => return session_error_response(error),
        }
    } else {
        match resolve_anonymous(&state,&headers,false).await {
            Ok(session) => json!({"mode":if session.ephemeral {"ephemeral"} else {"anonymous"},"anonymous":true,"expires_in":session.max_age,"login_available":state.config.login_enabled}),
            Err(StatusCode::UNAUTHORIZED) => json!({"mode":"choose","ephemeral_hours":state.config.ephemeral_hours,"login_available":state.config.login_enabled}),
            Err(status) => return anonymous_error(status),
        }
    };
    let mut response=no_store_json(StatusCode::OK,payload);
    if cookie_value(&headers,&state.config.csrf_cookie_name).is_none() {
        let mut config=(*state.config).clone(); config.cookie_domain=None;
        append_cookie(&mut response,&build_cookie(&config.csrf_cookie_name,&random_token(32),"/",180*24*3600,false,&config));
    }
    response
}

async fn end_access_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if validate_mutation_request(&state.config,&headers).is_err() { return anonymous_error(StatusCode::FORBIDDEN); }
    // Explicit exit expires the temporary capability; deletion runs in the catalog.
    if let Some(token)=cookie_value(&headers,VISITOR_COOKIE) {
        let result=state.http_client.post(format!("{}/internal/anonymous/end",state.config.library_url.trim_end_matches('/')))
            .timeout(Duration::from_secs(5)).json(&json!({"token":token,"create":false})).send().await;
        if !result.is_ok_and(|r|r.status().is_success()) { return anonymous_error(StatusCode::SERVICE_UNAVAILABLE); }
    }
    let mut response=auth_logout_handler(State(state.clone()),headers).await;
    let mut config=(*state.config).clone(); config.cookie_domain=None;
    append_cookie(&mut response,&delete_cookie(VISITOR_COOKIE,"/",&config));
    *response.status_mut()=StatusCode::OK;
    *response.body_mut()=Body::from("{}");
    response
}

async fn require_anonymous_session(State(state): State<AppState>, mut req: Request, next: Next) -> Response {
    if !matches!(*req.method(), Method::GET | Method::HEAD | Method::OPTIONS)
        && validate_mutation_request(&state.config,req.headers()).is_err() {
        return anonymous_error(StatusCode::FORBIDDEN);
    }
    // A stale or failed account session must never silently fall back to a guest.
    if state.config.login_enabled && cookie_value(req.headers(),&state.config.session_cookie_name).is_some() {
        return match authenticate_request(&state,req.headers()).await {
            Ok((_,principal)) => { req.extensions_mut().insert(principal); next.run(req).await },
            Err(error) => session_error_response(error),
        };
    }
    match resolve_anonymous(&state,req.headers(),false).await {
        Ok(session) => {
            req.extensions_mut().insert(Principal {subject: session.subject});
            next.run(req).await
        }
        Err(status) => anonymous_error(status),
    }
}

fn spawn_session_cleanup(state: AppState) {
    tokio::spawn(async move {
        let active_ttl = Duration::from_secs(300);
        loop {
            tokio::time::sleep(Duration::from_secs(60)).await;
            let now = Instant::now();
            state
                .sessions
                .retain(|_, session| session.refresh_expires_at > now);
            state
                .oauth_transactions
                .retain(|_, transaction| transaction.expires_at > now);
            state
                .active_users
                .retain(|_, last_seen| now.duration_since(*last_seen) < active_ttl);
            state
                .refresh_locks
                .retain(|key, _| state.sessions.contains_key(key));
        }
    });
}

async fn health_handler() -> impl IntoResponse {
    no_store_json(StatusCode::OK, json!({"status": "ok"}))
}

async fn auth_login_handler(State(state): State<AppState>) -> Response {
    if state.oauth_transactions.len() >= MAX_PENDING_OAUTH_TRANSACTIONS {
        let now = Instant::now();
        state
            .oauth_transactions
            .retain(|_, transaction| transaction.expires_at > now);
        if state.oauth_transactions.len() >= MAX_PENDING_OAUTH_TRANSACTIONS {
            return no_store_json(
                StatusCode::TOO_MANY_REQUESTS,
                json!({"error": "Demasiados inicios de sesion pendientes"}),
            );
        }
    }
    let oauth_state = random_token(32);
    let verifier = random_token(48);
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    state.oauth_transactions.insert(
        session_key(&oauth_state),
        OAuthTransaction {
            verifier_hash: session_key(&verifier),
            expires_at: Instant::now() + state.config.oauth_transaction_ttl,
        },
    );
    let mut authorize_url = state.config.auth_portal_authorize_url.clone();
    authorize_url
        .query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", CLIENT_ID)
        .append_pair("redirect_uri", state.config.callback_url.as_str())
        .append_pair("state", &oauth_state)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "S256");

    let mut response = redirect_response(authorize_url.as_str());
    append_cookie(
        &mut response,
        &build_cookie(
            &state.config.oauth_state_cookie_name,
            &oauth_state,
            "/auth/callback",
            state.config.oauth_transaction_ttl.as_secs(),
            true,
            &state.config,
        ),
    );
    append_cookie(
        &mut response,
        &build_cookie(
            &state.config.oauth_verifier_cookie_name,
            &verifier,
            "/auth/callback",
            state.config.oauth_transaction_ttl.as_secs(),
            true,
            &state.config,
        ),
    );
    add_no_store_headers(response.headers_mut());
    response
}

async fn auth_callback_handler(
    State(state): State<AppState>,
    Query(query): Query<CallbackQuery>,
    headers: HeaderMap,
) -> Response {
    match complete_callback(&state, &query, &headers).await {
        Ok((session_cookie, csrf_token, refresh_ttl)) => {
            let mut response = redirect_response(state.config.frontend_url.as_str());
            append_cookie(
                &mut response,
                &build_cookie(
                    &state.config.session_cookie_name,
                    &session_cookie,
                    "/",
                    refresh_ttl,
                    true,
                    &state.config,
                ),
            );
            append_cookie(
                &mut response,
                &build_cookie(
                    &state.config.csrf_cookie_name,
                    &csrf_token,
                    "/",
                    refresh_ttl,
                    false,
                    &state.config,
                ),
            );
            clear_oauth_transaction_cookies(&mut response, &state.config);
            add_no_store_headers(response.headers_mut());
            response
        }
        Err(status) => {
            let mut response = no_store_json(
                status,
                json!({"error": "No fue posible completar el inicio de sesion"}),
            );
            clear_oauth_transaction_cookies(&mut response, &state.config);
            response
        }
    }
}

async fn complete_callback(
    state: &AppState,
    query: &CallbackQuery,
    headers: &HeaderMap,
) -> std::result::Result<(String, String, u64), StatusCode> {
    if query.code.len() < 20 || query.code.len() > 256 {
        return Err(StatusCode::BAD_REQUEST);
    }
    let expected_state = cookie_value(headers, &state.config.oauth_state_cookie_name)
        .ok_or(StatusCode::BAD_REQUEST)?;
    let verifier = cookie_value(headers, &state.config.oauth_verifier_cookie_name)
        .ok_or(StatusCode::BAD_REQUEST)?;
    if !constant_time_eq(query.state.as_bytes(), expected_state.as_bytes())
        || verifier.len() < 43
        || verifier.len() > 128
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let transaction = state
        .oauth_transactions
        .remove(&session_key(&query.state))
        .map(|(_, value)| value)
        .ok_or(StatusCode::BAD_REQUEST)?;
    if transaction.expires_at <= Instant::now()
        || !constant_time_eq(
            transaction.verifier_hash.as_bytes(),
            session_key(&verifier).as_bytes(),
        )
    {
        return Err(StatusCode::BAD_REQUEST);
    }

    let token_url = identity_endpoint(&state.config.identity_url, "oauth/token")
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    let response = state
        .http_client
        .post(token_url)
        .header("Accept", "application/json")
        .header("X-Device-Name", "SmartDoc web BFF")
        .form(&[
            ("grant_type", "authorization_code"),
            ("code", query.code.as_str()),
            ("redirect_uri", state.config.callback_url.as_str()),
            ("client_id", CLIENT_ID),
            ("code_verifier", verifier.as_str()),
        ])
        .send()
        .await
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    if !response.status().is_success() {
        return Err(if response.status().is_client_error() {
            StatusCode::BAD_REQUEST
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        });
    }
    let token_value: serde_json::Value = response
        .json()
        .await
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    let refresh_candidate = token_value
        .get("refresh_token")
        .and_then(serde_json::Value::as_str)
        .filter(|value| (40..=256).contains(&value.len()))
        .map(str::to_string);
    let token: CentralTokenResponse = match serde_json::from_value(token_value) {
        Ok(token) => token,
        Err(_) => {
            if let Some(refresh_token) = refresh_candidate {
                revoke_central_refresh(state, refresh_token).await;
            }
            return Err(StatusCode::SERVICE_UNAVAILABLE);
        }
    };
    if validate_token_response(&token).is_err() {
        revoke_central_refresh(state, token.refresh_token).await;
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    let central_session = session_from_token(token);
    if let Err(error) = introspect_access_token(state, &central_session.access_token).await {
        revoke_central_refresh(state, central_session.refresh_token).await;
        return Err(match error {
            SessionError::Unauthorized => StatusCode::UNAUTHORIZED,
            SessionError::IdentityUnavailable => StatusCode::SERVICE_UNAVAILABLE,
        });
    }

    let raw_session = random_token(32);
    let key = session_key(&raw_session);
    let refresh_ttl = central_session
        .refresh_expires_at
        .saturating_duration_since(Instant::now())
        .as_secs()
        .max(1);
    revoke_previous_browser_session(state, headers).await;
    state.sessions.insert(key, central_session);
    Ok((raw_session, random_token(32), refresh_ttl))
}

async fn revoke_previous_browser_session(state: &AppState, headers: &HeaderMap) {
    let Some(raw_cookie) = cookie_value(headers, &state.config.session_cookie_name) else {
        return;
    };
    let key = session_key(&raw_cookie);
    if let Some((_, old_session)) = state.sessions.remove(&key) {
        revoke_central_refresh(state, old_session.refresh_token).await;
    }
    state.refresh_locks.remove(&key);
}

async fn revoke_central_refresh(state: &AppState, refresh_token: String) {
    let Ok(logout_url) = identity_endpoint(&state.config.identity_url, "auth/logout/refresh")
    else {
        return;
    };
    let result = state
        .http_client
        .post(logout_url)
        .json(&json!({"refresh_token": refresh_token}))
        .send()
        .await;
    if let Err(error) = result {
        warn!(error = %error, "No se pudo confirmar la revocacion central");
    }
}

async fn session_me_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    match authenticate_request(&state, &headers).await {
        Ok((_, principal)) => no_store_json(StatusCode::OK, json!(principal)),
        Err(error) => session_error_response(error),
    }
}

async fn session_refresh_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if validate_mutation_request(&state.config, &headers).is_err() {
        return no_store_json(
            StatusCode::FORBIDDEN,
            json!({"error": "Solicitud rechazada"}),
        );
    }
    let Some(raw_cookie) = cookie_value(&headers, &state.config.session_cookie_name) else {
        return session_error_response(SessionError::Unauthorized);
    };
    let key = session_key(&raw_cookie);
    match refresh_central_session(&state, &key, None).await {
        Ok(_) => match authenticate_session_key(&state, &key).await {
            Ok(principal) => no_store_json(StatusCode::OK, json!(principal)),
            Err(error) => session_error_response(error),
        },
        Err(error) => session_error_response(error),
    }
}

async fn auth_logout_handler(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if validate_mutation_request(&state.config, &headers).is_err() {
        return no_store_json(
            StatusCode::FORBIDDEN,
            json!({"error": "Solicitud rechazada"}),
        );
    }
    if let Some(raw_cookie) = cookie_value(&headers, &state.config.session_cookie_name) {
        let key = session_key(&raw_cookie);
        if let Some((_, session)) = state.sessions.remove(&key) {
            revoke_central_refresh(&state, session.refresh_token).await;
        }
        state.refresh_locks.remove(&key);
    }
    let mut response = StatusCode::NO_CONTENT.into_response();
    append_cookie(
        &mut response,
        &delete_cookie(&state.config.session_cookie_name, "/", &state.config),
    );
    append_cookie(
        &mut response,
        &delete_cookie(&state.config.csrf_cookie_name, "/", &state.config),
    );
    add_no_store_headers(response.headers_mut());
    response
}

async fn require_central_session(
    State(state): State<AppState>,
    mut req: Request,
    next: Next,
) -> Response {
    if matches!(
        req.uri().path(),
        "/health"
            | "/metrics"
            | "/auth/login"
            | "/auth/callback"
            | "/auth/logout"
            | "/session/me"
            | "/session/refresh"
    ) || req.method() == Method::OPTIONS
    {
        return next.run(req).await;
    }
    if !matches!(*req.method(), Method::GET | Method::HEAD | Method::OPTIONS)
        && validate_mutation_request(&state.config, req.headers()).is_err()
    {
        return no_store_json(
            StatusCode::FORBIDDEN,
            json!({"error": "Solicitud rechazada"}),
        );
    }
    match authenticate_request(&state, req.headers()).await {
        Ok((_, principal)) => {
            state
                .active_users
                .insert(principal.subject.clone(), Instant::now());
            req.extensions_mut().insert(principal);
            next.run(req).await
        }
        Err(error) => session_error_response(error),
    }
}

async fn authenticate_request(
    state: &AppState,
    headers: &HeaderMap,
) -> std::result::Result<(String, Principal), SessionError> {
    let raw_cookie = cookie_value(headers, &state.config.session_cookie_name)
        .ok_or(SessionError::Unauthorized)?;
    let key = session_key(&raw_cookie);
    let principal = authenticate_session_key(state, &key).await?;
    Ok((key, principal))
}

async fn authenticate_session_key(
    state: &AppState,
    key: &str,
) -> std::result::Result<Principal, SessionError> {
    let mut session = state
        .sessions
        .get(key)
        .map(|entry| entry.clone())
        .ok_or(SessionError::Unauthorized)?;
    if session.refresh_expires_at <= Instant::now() {
        state.sessions.remove(key);
        return Err(SessionError::Unauthorized);
    }
    if session.access_expires_at <= Instant::now() + Duration::from_secs(5) {
        session = refresh_central_session(state, key, Some(&session.access_token)).await?;
    }
    match introspect_access_token(state, &session.access_token).await {
        Ok(principal) => Ok(principal),
        Err(SessionError::Unauthorized) => {
            let refreshed =
                refresh_central_session(state, key, Some(&session.access_token)).await?;
            introspect_access_token(state, &refreshed.access_token).await
        }
        Err(error) => Err(error),
    }
}

async fn introspect_access_token(
    state: &AppState,
    access_token: &str,
) -> std::result::Result<Principal, SessionError> {
    let url = identity_endpoint(&state.config.identity_url, "auth/introspect")
        .map_err(|_| SessionError::IdentityUnavailable)?;
    let response = state
        .http_client
        .get(url)
        .bearer_auth(access_token)
        .header("X-Resource-Audience", RESOURCE_AUDIENCE)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|_| SessionError::IdentityUnavailable)?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED
        || response.status() == reqwest::StatusCode::FORBIDDEN
    {
        return Err(SessionError::Unauthorized);
    }
    if !response.status().is_success() {
        return Err(SessionError::IdentityUnavailable);
    }
    let central: CentralPrincipal = response
        .json()
        .await
        .map_err(|_| SessionError::IdentityUnavailable)?;
    if !central.is_active {
        return Err(SessionError::Unauthorized);
    }
    let subject = Uuid::parse_str(&central.id).map_err(|_| SessionError::IdentityUnavailable)?;
    if subject.to_string() != central.id.to_ascii_lowercase() {
        return Err(SessionError::IdentityUnavailable);
    }
    Ok(Principal {
        subject: subject.to_string(),
    })
}

async fn refresh_central_session(
    state: &AppState,
    key: &str,
    stale_access_token: Option<&str>,
) -> std::result::Result<CentralSession, SessionError> {
    let lock = state
        .refresh_locks
        .entry(key.to_string())
        .or_insert_with(|| Arc::new(Mutex::new(())))
        .clone();
    let _guard = lock.lock().await;
    let current = state
        .sessions
        .get(key)
        .map(|entry| entry.clone())
        .ok_or(SessionError::Unauthorized)?;
    if current.refresh_expires_at <= Instant::now() {
        state.sessions.remove(key);
        return Err(SessionError::Unauthorized);
    }
    if stale_access_token.is_some_and(|old| old != current.access_token) {
        return Ok(current);
    }
    let url = identity_endpoint(&state.config.identity_url, "auth/refresh")
        .map_err(|_| SessionError::IdentityUnavailable)?;
    let response = state
        .http_client
        .post(url)
        .header("Accept", "application/json")
        .json(&json!({"refresh_token": current.refresh_token}))
        .send()
        .await
        .map_err(|_| SessionError::IdentityUnavailable)?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED
        || response.status() == reqwest::StatusCode::BAD_REQUEST
    {
        state.sessions.remove(key);
        return Err(SessionError::Unauthorized);
    }
    if !response.status().is_success() {
        return Err(SessionError::IdentityUnavailable);
    }
    let token: CentralTokenResponse = response
        .json()
        .await
        .map_err(|_| SessionError::IdentityUnavailable)?;
    validate_token_response(&token).map_err(|_| SessionError::IdentityUnavailable)?;
    let replacement = session_from_token(token);
    state.sessions.insert(key.to_string(), replacement.clone());
    Ok(replacement)
}

fn validate_token_response(token: &CentralTokenResponse) -> Result<()> {
    if token.client_id != CLIENT_ID
        || !token.token_type.eq_ignore_ascii_case("bearer")
        || !(20..=8192).contains(&token.access_token.len())
        || !(40..=256).contains(&token.refresh_token.len())
        || !(1..=86_400).contains(&token.expires_in)
        || !(1..=31_622_400).contains(&token.refresh_expires_in)
    {
        return Err(anyhow!("respuesta de token central invalida"));
    }
    Ok(())
}

fn session_from_token(token: CentralTokenResponse) -> CentralSession {
    let now = Instant::now();
    CentralSession {
        access_token: token.access_token,
        refresh_token: token.refresh_token,
        access_expires_at: now + Duration::from_secs(token.expires_in),
        refresh_expires_at: now + Duration::from_secs(token.refresh_expires_in),
    }
}

fn validate_mutation_request(config: &Config, headers: &HeaderMap) -> Result<()> {
    let origin = headers
        .get(header::ORIGIN)
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| anyhow!("origin ausente"))?;
    if !constant_time_eq(origin.as_bytes(), config.frontend_origin.as_bytes()) {
        return Err(anyhow!("origin rechazado"));
    }
    let csrf_cookie = cookie_value(headers, &config.csrf_cookie_name)
        .ok_or_else(|| anyhow!("cookie csrf ausente"))?;
    let csrf_header = headers
        .get("X-CSRF-Token")
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| anyhow!("header csrf ausente"))?;
    if !constant_time_eq(csrf_cookie.as_bytes(), csrf_header.as_bytes()) {
        return Err(anyhow!("csrf rechazado"));
    }
    Ok(())
}

async fn process_document_handler(
    State(state): State<AppState>,
    Extension(principal): Extension<Principal>,
    req: Request,
) -> Response {
    proxy_handler(
        state.http_client.clone(),
        &state.config.doc_processor_url,
        req,
        state.config.max_body_bytes,
        &principal.subject,
    )
    .await
}

async fn library_handler(
    State(state): State<AppState>,
    Extension(principal): Extension<Principal>,
    req: Request,
) -> Response {
    proxy_handler(
        state.http_client.clone(),
        &state.config.library_url,
        req,
        state.config.max_body_bytes,
        &principal.subject,
    ).await
}

async fn llm_handler(
    State(state): State<AppState>,
    Extension(principal): Extension<Principal>,
    req: Request,
) -> Response {
    proxy_handler(
        state.http_client.clone(),
        &state.config.llm_service_url,
        req,
        state.config.max_body_bytes,
        &principal.subject,
    )
    .await
}

async fn metrics_handler(State(state): State<AppState>) -> impl IntoResponse {
    let cutoff = Instant::now() - Duration::from_secs(300);
    let active_user_count = state
        .active_users
        .iter()
        .filter(|entry| *entry.value() > cutoff)
        .count();
    no_store_json(
        StatusCode::OK,
        json!({
            "active_users_last_5_minutes": active_user_count,
            "active_server_sessions": state.sessions.len(),
        }),
    )
}

async fn proxy_handler(
    client: Client,
    base_url: &str,
    req: Request,
    max_body_bytes: usize,
    subject: &str,
) -> Response {
    let (parts, body) = req.into_parts();
    let path = parts.uri.path();
    let query = parts
        .uri
        .query()
        .map(|value| format!("?{value}"))
        .unwrap_or_default();
    let target_url = format!("{}{}{}", base_url.trim_end_matches('/'), path, query);
    let body = if path == "/api/documents" && parts.method == Method::POST {
        if parts.headers.get(header::CONTENT_LENGTH)
            .and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<usize>().ok())
            .is_some_and(|length| length > max_body_bytes) {
            return no_store_json(StatusCode::PAYLOAD_TOO_LARGE, json!({"error": "Archivo demasiado grande"}));
        }
        reqwest::Body::wrap_stream(
            Body::new(http_body_util::Limited::new(body, max_body_bytes)).into_data_stream()
        )
    } else {
        let body_bytes = match to_bytes(body, max_body_bytes).await {
        Ok(body) => body,
        Err(_) => {
            return no_store_json(
                StatusCode::PAYLOAD_TOO_LARGE,
                json!({"error": "Cuerpo invalido o demasiado grande"}),
            )
        }
    };
        reqwest::Body::from(body_bytes)
    };
    let method = method_to_reqwest(&parts.method);
    let mut req_headers = headers_axum_to_reqwest(&parts.headers);
    if let Ok(value) = reqwest::header::HeaderValue::from_str(subject) {
        req_headers.insert("x-smartdoc-subject", value);
    } else {
        return no_store_json(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"error": "Error interno"}),
        );
    }
    match client
        .request(method, &target_url)
        .headers(req_headers)
        .body(body)
        .send()
        .await
    {
        Ok(upstream) => {
            let status =
                StatusCode::from_u16(upstream.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let mut builder = Response::builder().status(status);
            if let Some(headers) = builder.headers_mut() {
                for (name, value) in upstream.headers() {
                    if !is_response_header_blocked(name.as_str()) {
                        if let (Ok(name), Ok(value)) = (
                            HeaderName::from_bytes(name.as_str().as_bytes()),
                            HeaderValue::from_bytes(value.as_bytes()),
                        ) {
                            headers.append(name, value);
                        }
                    }
                }
            }
            builder
                .body(Body::from_stream(upstream.bytes_stream()))
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(error) => {
            // reqwest errors can contain the full upstream URL (including its query).
            // Log only coarse diagnostics so credentials or user-supplied secrets
            // can never be copied into gateway logs.
            error!(
                timeout = error.is_timeout(),
                connect = error.is_connect(),
                "Servicio interno no disponible"
            );
            no_store_json(
                StatusCode::BAD_GATEWAY,
                json!({"error": "Servicio interno no disponible"}),
            )
        }
    }
}

fn headers_axum_to_reqwest(headers: &HeaderMap<HeaderValue>) -> reqwest::header::HeaderMap {
    let mut output = reqwest::header::HeaderMap::new();
    for (name, value) in headers {
        if !is_request_header_blocked(name.as_str()) {
            if let (Ok(name), Ok(value)) = (
                reqwest::header::HeaderName::from_bytes(name.as_str().as_bytes()),
                reqwest::header::HeaderValue::from_bytes(value.as_bytes()),
            ) {
                output.append(name, value);
            }
        }
    }
    output
}

fn is_request_header_blocked(name: &str) -> bool {
    is_hop_by_hop(name)
        || matches!(
            name.to_ascii_lowercase().as_str(),
            "authorization"
                | "cookie"
                | "origin"
                | "x-csrf-token"
                | "x-user-id"
                | "x-resource-audience"
                | "x-smartdoc-subject"
                | "x-forwarded-user"
        )
}

fn is_response_header_blocked(name: &str) -> bool {
    is_hop_by_hop(name)
        || matches!(
            name.to_ascii_lowercase().as_str(),
            "set-cookie" | "access-control-allow-origin" | "access-control-allow-credentials"
        )
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

fn method_to_reqwest(method: &Method) -> reqwest::Method {
    reqwest::Method::from_bytes(method.as_str().as_bytes()).unwrap_or(reqwest::Method::GET)
}

fn session_error_response(error: SessionError) -> Response {
    match error {
        SessionError::Unauthorized => no_store_json(
            StatusCode::UNAUTHORIZED,
            json!({"error": "Sesion no autenticada"}),
        ),
        SessionError::IdentityUnavailable => no_store_json(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error": "Identidad central no disponible"}),
        ),
    }
}

fn no_store_json(status: StatusCode, body: serde_json::Value) -> Response {
    let mut response = (status, Json(body)).into_response();
    add_no_store_headers(response.headers_mut());
    response
}

fn redirect_response(location: &str) -> Response {
    let mut response = StatusCode::SEE_OTHER.into_response();
    match HeaderValue::from_str(location) {
        Ok(value) => {
            response.headers_mut().insert(header::LOCATION, value);
        }
        Err(_) => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
    response
}

fn add_no_store_headers(headers: &mut HeaderMap) {
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    headers.insert(header::PRAGMA, HeaderValue::from_static("no-cache"));
    headers.insert("Referrer-Policy", HeaderValue::from_static("no-referrer"));
    headers.insert(
        "X-Content-Type-Options",
        HeaderValue::from_static("nosniff"),
    );
}

fn append_cookie(response: &mut Response, cookie: &str) {
    if let Ok(value) = HeaderValue::from_str(cookie) {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
}

fn build_cookie(
    name: &str,
    value: &str,
    path: &str,
    max_age: u64,
    http_only: bool,
    config: &Config,
) -> String {
    let mut cookie = format!("{name}={value}; Path={path}; Max-Age={max_age}; SameSite=Lax");
    if http_only {
        cookie.push_str("; HttpOnly");
    }
    if config.cookie_secure {
        cookie.push_str("; Secure");
    }
    if let Some(domain) = config.cookie_domain.as_deref() {
        cookie.push_str("; Domain=");
        cookie.push_str(domain);
    }
    cookie
}

fn delete_cookie(name: &str, path: &str, config: &Config) -> String {
    build_cookie(name, "", path, 0, true, config)
}

fn clear_oauth_transaction_cookies(response: &mut Response, config: &Config) {
    append_cookie(
        response,
        &delete_cookie(&config.oauth_state_cookie_name, "/auth/callback", config),
    );
    append_cookie(
        response,
        &delete_cookie(&config.oauth_verifier_cookie_name, "/auth/callback", config),
    );
}

fn cookie_value(headers: &HeaderMap, name: &str) -> Option<String> {
    for header_value in headers.get_all(header::COOKIE) {
        let Ok(raw) = header_value.to_str() else {
            continue;
        };
        for pair in raw.split(';') {
            let Some((cookie_name, value)) = pair.trim().split_once('=') else {
                continue;
            };
            if cookie_name == name && !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

fn random_token(bytes: usize) -> String {
    let mut raw = vec![0u8; bytes];
    OsRng.fill_bytes(&mut raw);
    URL_SAFE_NO_PAD.encode(raw)
}

fn session_key(raw_cookie: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(raw_cookie.as_bytes()))
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn identity_endpoint(base: &Url, path: &str) -> Result<Url> {
    base.join(path).context("URL de identidad no valida")
}

fn parse_base_url(name: &str, default: &str) -> Result<Url> {
    let mut url = parse_exact_url(name, default)?;
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    Ok(url)
}

fn parse_exact_url(name: &str, default: &str) -> Result<Url> {
    let raw = env::var(name).unwrap_or_else(|_| default.to_string());
    let url = Url::parse(raw.trim()).with_context(|| format!("{name} no es una URL valida"))?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || url.username() != ""
        || url.password().is_some()
        || url.fragment().is_some()
        || url.query().is_some()
    {
        return Err(anyhow!(
            "{name} debe ser una URL HTTP(S) sin credenciales, query ni fragmento"
        ));
    }
    Ok(url)
}

fn enforce_transport_security(name: &str, url: &Url) -> Result<()> {
    if url.scheme() == "https" || is_local_transport_host(url.host_str().unwrap_or_default()) {
        return Ok(());
    }
    Err(anyhow!("{name} requiere HTTPS fuera de loopback"))
}

fn validate_cookie_scope(callback: &Url, frontend: &Url, domain: Option<&str>) -> Result<()> {
    let callback_host = callback
        .host_str()
        .ok_or_else(|| anyhow!("callback sin host"))?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    let frontend_host = frontend
        .host_str()
        .ok_or_else(|| anyhow!("frontend sin host"))?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    match domain {
        None if callback_host != frontend_host => Err(anyhow!(
            "Gateway y frontend deben compartir host o configurar SMARTDOC_COOKIE_DOMAIN"
        )),
        Some(raw_domain) => {
            let cookie_domain = raw_domain
                .trim_start_matches('.')
                .trim_end_matches('.')
                .to_ascii_lowercase();
            let belongs =
                |host: &str| host == cookie_domain || host.ends_with(&format!(".{cookie_domain}"));
            if cookie_domain.is_empty() || !belongs(&callback_host) || !belongs(&frontend_host) {
                return Err(anyhow!(
                    "SMARTDOC_COOKIE_DOMAIN no contiene al gateway y al frontend"
                ));
            }
            Ok(())
        }
        None => Ok(()),
    }
}

fn is_local_transport_host(host: &str) -> bool {
    matches!(
        host,
        "localhost" | "127.0.0.1" | "[::1]" | "::1" | "host.docker.internal"
    ) || (!host.is_empty() && !host.contains('.') && !host.contains(':'))
}

fn url_origin(url: &Url) -> Result<String> {
    let host = url.host_str().ok_or_else(|| anyhow!("URL sin host"))?;
    let mut origin = format!("{}://{}", url.scheme(), host);
    if let Some(port) = url.port() {
        origin.push(':');
        origin.push_str(&port.to_string());
    }
    Ok(origin)
}

fn safe_cookie_name(name: &str, default: &str) -> Result<String> {
    let value = env::var(name).unwrap_or_else(|_| default.to_string());
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(anyhow!("{name} no es un nombre de cookie valido"));
    }
    Ok(value)
}

fn optional_bool(name: &str) -> Result<Option<bool>> {
    let Ok(raw) = env::var(name) else {
        return Ok(None);
    };
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(Some(true)),
        "0" | "false" | "no" | "off" => Ok(Some(false)),
        _ => Err(anyhow!("{name} debe ser true o false")),
    }
}

fn env_u64(name: &str, default: u64, min: u64, max: u64) -> Result<u64> {
    let value = env::var(name)
        .unwrap_or_else(|_| default.to_string())
        .parse::<u64>()
        .with_context(|| format!("{name} debe ser entero"))?;
    if !(min..=max).contains(&value) {
        return Err(anyhow!("{name} debe estar entre {min} y {max}"));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        extract::Form,
        routing::{any, post as axum_post},
    };
    use std::collections::HashMap;

    #[test]
    fn pkce_challenge_is_urlsafe_sha256() {
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        assert_eq!(challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    }

    #[test]
    fn cookie_parser_does_not_confuse_prefixes() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::COOKIE,
            HeaderValue::from_static("smartdoc_session_extra=no; smartdoc_session=yes"),
        );
        assert_eq!(
            cookie_value(&headers, "smartdoc_session").as_deref(),
            Some("yes")
        );
    }

    #[test]
    fn untrusted_identity_headers_are_always_removed() {
        for header in [
            "authorization",
            "cookie",
            "x-user-id",
            "x-resource-audience",
            "x-smartdoc-subject",
            "x-forwarded-user",
        ] {
            assert!(is_request_header_blocked(header));
        }
        assert!(!is_request_header_blocked("content-type"));
    }

    #[test]
    fn constant_time_comparison_requires_same_bytes() {
        assert!(constant_time_eq(b"same", b"same"));
        assert!(!constant_time_eq(b"same", b"diff"));
        assert!(!constant_time_eq(b"short", b"longer"));
    }

    #[test]
    fn cookie_scope_rejects_unrelated_hosts() {
        let callback = Url::parse("https://gateway.example.test/auth/callback").unwrap();
        let frontend = Url::parse("https://smartdoc.example.test/").unwrap();
        assert!(validate_cookie_scope(&callback, &frontend, None).is_err());
        assert!(validate_cookie_scope(&callback, &frontend, Some(".example.test")).is_ok());
        assert!(validate_cookie_scope(&callback, &frontend, Some(".unrelated.test")).is_err());
    }

    fn valid_token_body() -> serde_json::Value {
        json!({
            "access_token": "central-access-token",
            "refresh_token": "central-refresh-token-with-sufficient-entropy",
            "token_type": "bearer",
            "expires_in": 900,
            "refresh_expires_in": 3600,
            "session_id": "123e4567-e89b-12d3-a456-426614174009",
            "device_id": "smartdoc-test",
            "client_id": CLIENT_ID,
        })
    }

    fn assert_token_form(form: &HashMap<String, String>) {
        assert_eq!(
            form.get("grant_type").map(String::as_str),
            Some("authorization_code")
        );
        assert_eq!(form.get("client_id").map(String::as_str), Some(CLIENT_ID));
        assert!(form
            .get("code_verifier")
            .is_some_and(|value| value.len() >= 43));
    }

    async fn mock_token(Form(form): Form<HashMap<String, String>>) -> impl IntoResponse {
        assert_token_form(&form);
        Json(valid_token_body())
    }

    async fn mock_introspect(headers: HeaderMap) -> impl IntoResponse {
        assert_eq!(
            headers
                .get("X-Resource-Audience")
                .and_then(|value| value.to_str().ok()),
            Some(RESOURCE_AUDIENCE)
        );
        assert_eq!(
            headers
                .get("Authorization")
                .and_then(|value| value.to_str().ok()),
            Some("Bearer central-access-token")
        );
        Json(json!({
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "is_active": true,
        }))
    }

    async fn mock_logout() -> impl IntoResponse {
        StatusCode::NO_CONTENT
    }

    #[derive(Clone)]
    struct CallbackFailureMockState {
        token_body: serde_json::Value,
        introspect_status: StatusCode,
        introspect_body: serde_json::Value,
        revoked_refresh_tokens: Arc<Mutex<Vec<String>>>,
    }

    async fn configurable_token(
        State(state): State<CallbackFailureMockState>,
        Form(form): Form<HashMap<String, String>>,
    ) -> impl IntoResponse {
        assert_token_form(&form);
        Json(state.token_body)
    }

    async fn configurable_introspect(
        State(state): State<CallbackFailureMockState>,
        headers: HeaderMap,
    ) -> Response {
        assert_eq!(
            headers
                .get("X-Resource-Audience")
                .and_then(|value| value.to_str().ok()),
            Some(RESOURCE_AUDIENCE)
        );
        assert_eq!(
            headers
                .get("Authorization")
                .and_then(|value| value.to_str().ok()),
            Some("Bearer central-access-token")
        );
        (state.introspect_status, Json(state.introspect_body)).into_response()
    }

    async fn record_logout(
        State(state): State<CallbackFailureMockState>,
        Json(payload): Json<serde_json::Value>,
    ) -> StatusCode {
        let refresh_token = payload
            .get("refresh_token")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();
        state
            .revoked_refresh_tokens
            .lock()
            .await
            .push(refresh_token);
        StatusCode::NO_CONTENT
    }

    #[tokio::test]
    async fn anonymous_flow_keeps_capability_private_and_survives_gateway_restart() {
        const TOKEN: &str = "anonymous_capability_random_example_1234567890123";
        async fn resolver(Json(body): Json<serde_json::Value>) -> Response {
            if body["token"] == TOKEN {
                Json(json!({"subject":"123e4567-e89b-12d3-a456-426614174000", "token":null,"max_age":1000})).into_response()
            } else if body["create"] == true {
                Json(json!({"subject":"123e4567-e89b-12d3-a456-426614174000", "token":TOKEN,"max_age":1000})).into_response()
            } else { StatusCode::UNAUTHORIZED.into_response() }
        }
        let upstream = spawn_test_server(Router::new()
            .route("/internal/anonymous/session",post(resolver)).fallback(any(mock_product))).await;
        let mut config = Config::from_env().unwrap();
        config.anonymous=true;
        config.library_url=format!("http://{upstream}");
        let state = AppState {
            http_client: Client::new(), sessions: Arc::new(DashMap::new()),
            oauth_transactions: Arc::new(DashMap::new()),refresh_locks:Arc::new(DashMap::new()),
            active_users:Arc::new(DashMap::new()),config:Arc::new(config),
        };
        let origin=state.config.frontend_origin.clone();
        let gateway = spawn_test_server(build_app(state.clone()).unwrap()).await;
        let client=Client::new();
        let url=format!("http://{gateway}");
        assert_eq!(client.get(format!("{url}/api/documents")).send().await.unwrap().status(),401);
        let choose=client.get(format!("{url}/session/me")).send().await.unwrap();
        let csrf=named_set_cookie(&choose,"smartdoc_csrf");
        assert_eq!(choose.json::<serde_json::Value>().await.unwrap()["mode"],"choose");
        assert_eq!(client.post(format!("{url}/session/anonymous")).send().await.unwrap().status(),403);
        let bootstrap=client.post(format!("{url}/session/anonymous"))
            .header("Cookie",&csrf).header("Origin",&origin)
            .header("X-CSRF-Token",csrf.split_once('=').unwrap().1).send().await.unwrap();
        assert_eq!(bootstrap.status(),200);
        let visitor=named_set_cookie(&bootstrap,VISITOR_COOKIE);
        assert!(bootstrap.headers().get_all("set-cookie").iter().any(|c| c.to_str().unwrap().contains("HttpOnly")));
        let payload=bootstrap.json::<serde_json::Value>().await.unwrap();
        assert_eq!(payload["anonymous"],true);
        assert!(payload.get("subject").is_none());
        assert!(payload.get("token").is_none());
        let cookies=format!("{visitor}; {csrf}");
        let product=client.get(format!("{url}/api/documents")).header("Cookie",&cookies)
            .header("X-SmartDoc-Subject","attacker").send().await.unwrap();
        let body=product.json::<serde_json::Value>().await.unwrap();
        assert_eq!(body["subject"],"123e4567-e89b-12d3-a456-426614174000");
        assert_eq!(body["cookie_present"],false);
        assert_eq!(client.post(format!("{url}/api/events")).header("Cookie",&cookies).send().await.unwrap().status(),403);
        assert_eq!(client.post(format!("{url}/api/events")).header("Cookie",&cookies)
            .header("Origin",&origin).header("X-CSRF-Token",csrf.split_once('=').unwrap().1)
            .send().await.unwrap().status(),200);
        assert_eq!(client.get(format!("{url}/session/me")).header("Sec-Fetch-Site","cross-site").send().await.unwrap().status(),403);
        assert_eq!(client.get(format!("{url}/auth/login")).send().await.unwrap().status(),503);
        for path in ["/internal/anonymous/session","/process_document/"] {
            assert_eq!(client.get(format!("{url}{path}")).send().await.unwrap().status(),404);
        }
        assert_eq!(client.get(format!("{url}/api/documents")).header("Cookie","sara_visitor=attacker").send().await.unwrap().status(),401);
        let restarted=spawn_test_server(build_app(state).unwrap()).await;
        assert_eq!(client.get(format!("http://{restarted}/api/documents")).header("Cookie",&cookies).send().await.unwrap().status(),200);
        let revisit=client.get(format!("{url}/session/me")).header("Cookie",&cookies).send().await.unwrap();
        assert_eq!(revisit.status(),200);
        assert!(revisit.headers().get("set-cookie").is_none());
    }

    #[tokio::test]
    async fn library_upload_declared_limit_is_enforced_before_forwarding() {
        let request = Request::builder().method(Method::POST).uri("/api/documents")
            .header(header::CONTENT_LENGTH, "100").body(Body::from("small")).unwrap();
        let response = proxy_handler(Client::new(), "http://127.0.0.1:1", request, 10,
            "123e4567-e89b-12d3-a456-426614174000").await;
        assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
    }

    async fn mock_product(headers: HeaderMap, body: axum::body::Bytes) -> impl IntoResponse {
        Json(json!({
            "subject": headers.get("X-SmartDoc-Subject").and_then(|value| value.to_str().ok()),
            "x_user_id_present": headers.contains_key("X-User-ID"),
            "audience_present": headers.contains_key("X-Resource-Audience"),
            "authorization_present": headers.contains_key("Authorization"),
            "cookie_present": headers.contains_key("Cookie"),
            "body_bytes": body.len(),
        }))
    }

    async fn spawn_test_server(router: Router) -> SocketAddr {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });
        address
    }

    fn named_set_cookie(response: &reqwest::Response, name: &str) -> String {
        response
            .headers()
            .get_all("Set-Cookie")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .find_map(|value| {
                value
                    .split(';')
                    .next()
                    .filter(|pair| pair.starts_with(&format!("{name}=")))
                    .map(str::to_string)
            })
            .unwrap_or_else(|| panic!("cookie {name} ausente"))
    }

    async fn assert_failed_callback_revokes_grant(
        token_body: serde_json::Value,
        introspect_status: StatusCode,
        introspect_body: serde_json::Value,
        expected_status: StatusCode,
    ) {
        let revoked_refresh_tokens = Arc::new(Mutex::new(Vec::new()));
        let mock_state = CallbackFailureMockState {
            token_body,
            introspect_status,
            introspect_body,
            revoked_refresh_tokens: revoked_refresh_tokens.clone(),
        };
        let upstream = Router::new()
            .route("/oauth/token", axum_post(configurable_token))
            .route("/auth/introspect", get(configurable_introspect))
            .route("/auth/logout/refresh", axum_post(record_logout))
            .with_state(mock_state);
        let upstream_addr = spawn_test_server(upstream).await;
        let identity_url = Url::parse(&format!("http://{upstream_addr}/")).unwrap();
        let config = Arc::new(Config {
            anonymous: false,
            login_enabled: true,
            ephemeral_hours:24,
            auth_portal_authorize_url: Url::parse("http://127.0.0.1:3000/authorize").unwrap(),
            identity_url: identity_url.clone(),
            callback_url: Url::parse("http://127.0.0.1:8043/auth/callback").unwrap(),
            frontend_url: Url::parse("http://127.0.0.1:8501/").unwrap(),
            frontend_origin: "http://127.0.0.1:8501".to_string(),
            session_cookie_name: "smartdoc_session".to_string(),
            csrf_cookie_name: "smartdoc_csrf".to_string(),
            oauth_state_cookie_name: "smartdoc_oauth_state".to_string(),
            oauth_verifier_cookie_name: "smartdoc_oauth_verifier".to_string(),
            cookie_domain: None,
            cookie_secure: false,
            oauth_transaction_ttl: Duration::from_secs(300),
            doc_processor_url: identity_url.to_string(),
            llm_service_url: identity_url.to_string(),
            library_url: identity_url.to_string(),
            max_body_bytes: 1024 * 1024,
            listen_addr: "127.0.0.1:0".parse().unwrap(),
        });
        let state = AppState {
            http_client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
            sessions: Arc::new(DashMap::new()),
            oauth_transactions: Arc::new(DashMap::new()),
            refresh_locks: Arc::new(DashMap::new()),
            active_users: Arc::new(DashMap::new()),
            config,
        };
        let oauth_state = random_token(32);
        let verifier = random_token(48);
        state.oauth_transactions.insert(
            session_key(&oauth_state),
            OAuthTransaction {
                verifier_hash: session_key(&verifier),
                expires_at: Instant::now() + Duration::from_secs(300),
            },
        );
        let mut headers = HeaderMap::new();
        headers.insert(
            header::COOKIE,
            HeaderValue::from_str(&format!(
                "smartdoc_oauth_state={oauth_state}; smartdoc_oauth_verifier={verifier}"
            ))
            .unwrap(),
        );
        let result = complete_callback(
            &state,
            &CallbackQuery {
                code: "valid-authorization-code-12345".to_string(),
                state: oauth_state,
            },
            &headers,
        )
        .await;

        assert_eq!(result.unwrap_err(), expected_status);
        assert!(state.sessions.is_empty());
        assert_eq!(
            revoked_refresh_tokens.lock().await.as_slice(),
            ["central-refresh-token-with-sufficient-entropy"]
        );
    }

    #[tokio::test]
    async fn callback_revokes_new_grant_for_every_post_exchange_failure() {
        assert_failed_callback_revokes_grant(
            valid_token_body(),
            StatusCode::UNAUTHORIZED,
            json!({"error": "unauthorized"}),
            StatusCode::UNAUTHORIZED,
        )
        .await;
        assert_failed_callback_revokes_grant(
            valid_token_body(),
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error": "unavailable"}),
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .await;
        assert_failed_callback_revokes_grant(
            valid_token_body(),
            StatusCode::OK,
            json!({"id": 123, "is_active": "not-a-boolean"}),
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .await;
        let mut wrong_client = valid_token_body();
        wrong_client["client_id"] = json!("some-other-client");
        assert_failed_callback_revokes_grant(
            wrong_client,
            StatusCode::OK,
            json!({"id": "123e4567-e89b-12d3-a456-426614174000", "is_active": true}),
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .await;
        assert_failed_callback_revokes_grant(
            json!({
                "refresh_token": "central-refresh-token-with-sufficient-entropy",
                "token_type": "bearer",
                "expires_in": 900,
                "refresh_expires_in": 3600,
                "client_id": CLIENT_ID,
            }),
            StatusCode::OK,
            json!({"id": "123e4567-e89b-12d3-a456-426614174000", "is_active": true}),
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .await;
    }

    #[tokio::test]
    async fn bff_flow_keeps_tokens_server_side_and_strips_spoofed_identity() { central_flow(false).await; }

    #[tokio::test]
    async fn mixed_mode_supports_central_pkce_and_account_isolation() { central_flow(true).await; }

    async fn central_flow(mixed: bool) {
        let upstream = Router::new()
            .route("/oauth/token", axum_post(mock_token))
            .route("/auth/introspect", get(mock_introspect))
            .route("/auth/logout/refresh", axum_post(mock_logout))
            .fallback(any(mock_product));
        let upstream_addr = spawn_test_server(upstream).await;

        let gateway_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let gateway_addr = gateway_listener.local_addr().unwrap();
        let upstream_url = Url::parse(&format!("http://{upstream_addr}/")).unwrap();
        let callback_url = Url::parse(&format!("http://{gateway_addr}/auth/callback")).unwrap();
        let config = Arc::new(Config {
            anonymous: mixed,
            login_enabled: true,
            ephemeral_hours:24,
            auth_portal_authorize_url: Url::parse("http://127.0.0.1:3000/authorize").unwrap(),
            identity_url: upstream_url.clone(),
            callback_url: callback_url.clone(),
            frontend_url: Url::parse("http://127.0.0.1:8501/").unwrap(),
            frontend_origin: "http://127.0.0.1:8501".to_string(),
            session_cookie_name: "smartdoc_session".to_string(),
            csrf_cookie_name: "smartdoc_csrf".to_string(),
            oauth_state_cookie_name: "smartdoc_oauth_state".to_string(),
            oauth_verifier_cookie_name: "smartdoc_oauth_verifier".to_string(),
            cookie_domain: None,
            cookie_secure: false,
            oauth_transaction_ttl: Duration::from_secs(300),
            doc_processor_url: upstream_url.to_string(),
            llm_service_url: upstream_url.to_string(),
            library_url: upstream_url.to_string(),
            max_body_bytes: 1024 * 1024,
            listen_addr: gateway_addr,
        });
        let state = AppState {
            http_client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
            sessions: Arc::new(DashMap::new()),
            oauth_transactions: Arc::new(DashMap::new()),
            refresh_locks: Arc::new(DashMap::new()),
            active_users: Arc::new(DashMap::new()),
            config,
        };
        let gateway = build_app(state).unwrap();
        tokio::spawn(async move {
            axum::serve(gateway_listener, gateway).await.unwrap();
        });

        let client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap();
        let gateway_base = format!("http://{gateway_addr}");

        let login = client
            .get(format!("{gateway_base}/auth/login"))
            .send()
            .await
            .unwrap();
        assert_eq!(login.status(), reqwest::StatusCode::SEE_OTHER);
        let state_cookie = named_set_cookie(&login, "smartdoc_oauth_state");
        let verifier_cookie = named_set_cookie(&login, "smartdoc_oauth_verifier");
        assert!(login
            .headers()
            .get_all("Set-Cookie")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .all(|cookie| cookie.contains("HttpOnly")));
        let authorize =
            Url::parse(login.headers().get("Location").unwrap().to_str().unwrap()).unwrap();
        let oauth_state = authorize
            .query_pairs()
            .find_map(|(key, value)| (key == "state").then(|| value.into_owned()))
            .unwrap();

        let callback = client
            .get(format!(
                "{gateway_base}/auth/callback?code=valid-authorization-code-12345&state={oauth_state}"
            ))
            .header("Cookie", format!("{state_cookie}; {verifier_cookie}"))
            .send()
            .await
            .unwrap();
        assert_eq!(callback.status(), reqwest::StatusCode::SEE_OTHER);
        assert_eq!(
            callback
                .headers()
                .get("Location")
                .unwrap()
                .to_str()
                .unwrap(),
            "http://127.0.0.1:8501/"
        );
        assert!(!callback
            .headers()
            .get("Location")
            .unwrap()
            .to_str()
            .unwrap()
            .contains("token"));
        let callback_cookies: Vec<_> = callback
            .headers()
            .get_all("Set-Cookie")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .collect();
        let session_cookie_header = callback_cookies
            .iter()
            .find(|cookie| cookie.starts_with("smartdoc_session="))
            .expect("session cookie ausente");
        assert!(session_cookie_header.contains("HttpOnly"));
        assert!(session_cookie_header.contains("SameSite=Lax"));
        assert!(!session_cookie_header.contains("central-access-token"));
        let session_cookie = named_set_cookie(&callback, "smartdoc_session");
        let csrf_cookie = named_set_cookie(&callback, "smartdoc_csrf");
        assert!(!session_cookie.contains("central-access-token"));
        let csrf_value = csrf_cookie.split_once('=').unwrap().1.to_string();
        let browser_cookies = format!("{session_cookie}; {csrf_cookie}");

        let me = client
            .get(format!("{gateway_base}/session/me"))
            .header("Cookie", &browser_cookies)
            .send()
            .await
            .unwrap();
        assert_eq!(me.status(), reqwest::StatusCode::OK);
        let me=me.json::<serde_json::Value>().await.unwrap();
        if mixed { assert_eq!(me["mode"],"account"); } else {
            assert_eq!(me["subject"],"123e4567-e89b-12d3-a456-426614174000");
        }

        let product = client
            .get(format!("{gateway_base}/api/documents"))
            .header("Cookie", &browser_cookies)
            .header("Authorization", "Bearer attacker")
            .header("X-User-ID", "attacker")
            .header("X-Resource-Audience", "other-product")
            .header("X-SmartDoc-Subject", "attacker")
            .send()
            .await
            .unwrap();
        assert_eq!(product.status(), reqwest::StatusCode::OK);
        let product = product.json::<serde_json::Value>().await.unwrap();
        assert_eq!(product["subject"], "123e4567-e89b-12d3-a456-426614174000");
        assert_eq!(product["x_user_id_present"], false);
        assert_eq!(product["audience_present"], false);
        assert_eq!(product["authorization_present"], false);
        assert_eq!(product["cookie_present"], false);

        let rejected = client
            .post(format!("{gateway_base}/api/documents"))
            .header("Cookie", &browser_cookies)
            .header("Origin", "http://127.0.0.1:8501")
            .header("X-CSRF-Token", "wrong")
            .send()
            .await
            .unwrap();
        assert_eq!(rejected.status(), reqwest::StatusCode::FORBIDDEN);

        let accepted = client
            .post(format!("{gateway_base}/api/documents"))
            .header("Cookie", &browser_cookies)
            .header("Origin", "http://127.0.0.1:8501")
            .header("X-CSRF-Token", &csrf_value)
            .body("%PDF-1.7 fixture")
            .send()
            .await
            .unwrap();
        assert_eq!(accepted.status(), reqwest::StatusCode::OK);
        assert_eq!(accepted.json::<serde_json::Value>().await.unwrap()["body_bytes"], 16);

        let logout = client
            .post(format!("{gateway_base}{}",if mixed {"/session/end"} else {"/auth/logout"}))
            .header("Cookie", &browser_cookies)
            .header("Origin", "http://127.0.0.1:8501")
            .header("X-CSRF-Token", csrf_value)
            .send()
            .await
            .unwrap();
        assert_eq!(logout.status(), if mixed {reqwest::StatusCode::OK} else {reqwest::StatusCode::NO_CONTENT});
        let after_logout = client
            .get(format!("{gateway_base}/session/me"))
            .header("Cookie", browser_cookies)
            .send()
            .await
            .unwrap();
        assert_eq!(after_logout.status(), if mixed {reqwest::StatusCode::OK} else {reqwest::StatusCode::UNAUTHORIZED});
        if mixed { assert_eq!(after_logout.json::<serde_json::Value>().await.unwrap()["mode"],"choose"); }
    }
}
