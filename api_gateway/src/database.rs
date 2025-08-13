// api_gateway/src/database.rs

use anyhow::Result;
use rusqlite::Error as SqliteError;
use std::{path::Path, time::Duration as StdDuration};

/// Conexión asíncrona a SQLite gestionada por un hilo dedicado.
pub type Db = tokio_rusqlite::Connection;

/// Inicializa la base de datos SQLite en `path`.
/// - Abre/crea el archivo.
/// - Configura PRAGMAs recomendados para entorno web.
/// - Crea el esquema mínimo (`users`).
pub async fn init(path: &Path) -> Result<Db> {
    // Crea el directorio si no existe (por si pasas algo como ./data/smartdoc.db)
    if let Some(dir) = path.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir)?;
        }
    }

    // Abre conexión asíncrona (opera en un hilo dedicado)
    let conn = tokio_rusqlite::Connection::open(path).await?;

    // Configuración y schema dentro del hilo de SQLite
    conn.call(|c| {
        // Modo WAL: mejor concurrencia para lecturas/escrituras mixtas
        c.pragma_update(None, "journal_mode", "WAL")?;
        // Balance seguridad/rendimiento. Para máxima durabilidad usa "FULL".
        c.pragma_update(None, "synchronous", "NORMAL")?;
        // Llaves foráneas (por si amplías el schema)
        c.pragma_update(None, "foreign_keys", "ON")?;
        // Autocheckpoint del WAL (cada ~1000 páginas)
        let _ = c.pragma_update(None, "wal_autocheckpoint", &1000i64);

        // Evita busy loops en contención de bloqueo
        c.busy_timeout(StdDuration::from_secs(5))?;

        // Esquema mínimo
        c.execute(
            "CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL UNIQUE,
                -- ISO-8601 UTC facilita orden y parsing
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )",
            [],
        )?;

        Ok::<_, SqliteError>(())
    }).await?;

    println!("Base de datos inicializada correctamente en: {}", path.display());
    Ok(conn)
}

/// Inserta el usuario si no existe (idempotente).
pub async fn find_or_create_user(db: &Db, username: &str) -> Result<()> {
    // Movemos un String al hilo de SQLite
    let username = username.to_owned();

    db.call(move |c| {
        // INSERT OR IGNORE hace la operación idempotente
        c.execute(
            "INSERT OR IGNORE INTO users (username) VALUES (?1)",
            [&username],
        )?;
        Ok::<_, SqliteError>(())
    }).await?;

    Ok(())
}
