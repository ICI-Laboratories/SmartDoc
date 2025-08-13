// api_gateway/src/database.rs

use anyhow::Result;
use std::{path::Path, time::Duration as StdDuration};

pub type Db = tokio_rusqlite::Connection;

pub async fn init(path: &Path) -> Result<Db> {
    if let Some(dir) = path.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir)?;
        }
    }

    let conn = tokio_rusqlite::Connection::open(path).await?;

    // La closure devuelve tokio_rusqlite::Result, y convertimos errores con Into::into
    conn.call(|c: &mut rusqlite::Connection| -> tokio_rusqlite::Result<()> {
        c.pragma_update(None, "journal_mode", "WAL").map_err(Into::into)?;
        c.pragma_update(None, "synchronous", "NORMAL").map_err(Into::into)?;
        c.pragma_update(None, "foreign_keys", "ON").map_err(Into::into)?;
        let _ = c.pragma_update(None, "wal_autocheckpoint", &1000i64);
        c.busy_timeout(StdDuration::from_secs(5)).map_err(Into::into)?;

        c.execute(
            "CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )",
            [],
        ).map_err(Into::into)?;

        Ok(())
    }).await?; // <- un solo `?` (tokio_rusqlite::Result)

    println!("Base de datos inicializada correctamente en: {}", path.display());
    Ok(conn)
}

pub async fn find_or_create_user(db: &Db, username: &str) -> Result<()> {
    let username = username.to_owned();

    db.call(move |c: &mut rusqlite::Connection| -> tokio_rusqlite::Result<()> {
        c.execute(
            "INSERT OR IGNORE INTO users (username) VALUES (?1)",
            [&username],
        ).map_err(Into::into)?;
        Ok(())
    }).await?; // <- un solo `?`

    Ok(())
}
