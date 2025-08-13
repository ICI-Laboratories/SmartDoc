// api_gateway/src/database.rs

use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::Path;
use std::sync::Arc;
use tokio::sync::Mutex;

// Usamos un tipo `Db` para envolver nuestra conexión en un Mutex seguro para concurrencia.
// Arc (Atomically Reference Counted) permite que múltiples hilos compartan la propiedad de la conexión.
pub type Db = Arc<Mutex<Connection>>;

/// Inicializa la base de datos.
///
/// - Abre una conexión a un archivo SQLite en la ruta especificada.
/// - Si el archivo no existe, lo crea.
/// - Ejecuta una consulta para crear la tabla 'users' si no existe.
/// - Devuelve una conexión envuelta en Arc<Mutex> para un acceso seguro.
pub fn init(path: &Path) -> Result<Db> {
    // Abre la conexión. `open` crea el archivo si no existe.
    let conn = Connection::open(path)?;

    // PRAGMA para mejorar el rendimiento y la seguridad en modo concurrente.
    // WAL (Write-Ahead Logging) es el modo preferido para aplicaciones web.
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "NORMAL")?;

    // Crea la tabla 'users' si no existe.
    // `IF NOT EXISTS` previene errores si la tabla ya fue creada.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
        )",
        [], // No hay parámetros en esta consulta
    )?;

    println!("Base de datos inicializada correctamente en: {}", path.display());
    Ok(Arc::new(Mutex::new(conn)))
}

/// Registra un nuevo usuario o verifica si ya existe.
///
/// - Bloquea el Mutex para obtener acceso exclusivo a la conexión.
/// - Intenta insertar un nuevo usuario.
/// - Si el usuario ya existe (violación de la restricción `UNIQUE`), lo ignora y considera la operación un éxito.
/// - Esto hace que la función sea "idempotente": llamarla múltiples veces con el mismo username tiene el mismo resultado que llamarla una vez.
pub async fn find_or_create_user(db: &Db, username: &str) -> Result<()> {
    // Bloquea el Mutex para asegurar el acceso exclusivo.
    // El bloqueo se libera automáticamente cuando `conn_guard` sale del scope.
    let conn_guard = db.lock().await;

    // `?` propaga el error si la inserción falla por una razón que no sea la violación de unicidad.
    let result = conn_guard.execute(
        "INSERT OR IGNORE INTO users (username) VALUES (?1)",
        params![username],
    );
    
    match result {
        Ok(_) => Ok(()), // Éxito, ya sea insertado o ignorado.
        Err(e) => {
            // Si hay un error, lo registramos y lo propagamos.
            eprintln!("Error al intentar registrar al usuario '{}': {}", username, e);
            Err(e.into())
        }
    }
}