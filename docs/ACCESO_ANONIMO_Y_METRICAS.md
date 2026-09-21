# Acceso sin cuenta y medición de uso

## Experiencia

SARA DocReader ofrece un selector con dos opciones:

- **Usar temporalmente**: sin registro, con una biblioteca independiente por navegador. Las nuevas sesiones duran 24 horas por defecto (`SARA_EPHEMERAL_HOURS`, 1–168 horas). «Terminar y borrar espacio» revoca el acceso y elimina archivos y datos documentales del espacio temporal. Cerrar una pestaña no dispara el borrado; borrar cookies pierde el acceso, pero la limpieza programada sigue vigente.
- **Iniciar sesión**: integración preparada con el portal central SARA, desactivada por decisión del usuario. Con `SARA_LOGIN_ENABLED=false` el botón muestra «próximamente». Al habilitarlo, la biblioteca se vincula al UUID validado por el portal y cerrar sesión conserva los documentos.

No se mezclan ni transfieren automáticamente documentos entre espacios. Una cuenta inválida no se sustituye silenciosamente por una sesión temporal. Las bibliotecas anónimas creadas antes de este cambio conservan su política anterior de acceso de hasta 180 días y no se borran automáticamente.

El acceso temporal usa cookie HttpOnly, SameSite=Lax y Secure en HTTPS. El servidor genera el identificador y el secreto; PostgreSQL guarda sólo el SHA-256 del secreto. Las sesiones temporales sobreviven a reinicios del gateway. Las sesiones de cuenta actuales del gateway se mantienen en memoria: tras reiniciarlo se debe iniciar sesión de nuevo, pero la biblioteca persiste.

La visita inicial (`GET /session/me`) no crea una biblioteca: entrega el estado del selector y un token CSRF. La creación temporal requiere `POST /session/anonymous` con origen y CSRF válidos. Las rutas internas y los procesadores legados no se exponen por el gateway moderno.

La caducidad se revisa al iniciar y cada minuto mientras el catálogo funciona, aun con métricas desactivadas. Borra originales, chunks, resúmenes y trabajos de los espacios temporales. Si el servicio está detenido, se limpia al volver a arrancar. Los registros de sesión caducados se conservan siete días tras limpiar para rechazar peticiones en curso; nunca contienen el secreto original. La eliminación se refiere al almacenamiento activo: no purga copias de seguridad externas.

Para habilitar cuentas más adelante: configurar `SMARTDOC_AUTH_PORTAL_URL` (URL accesible al navegador), `SMARTDOC_IDENTITY_URL` (accesible al gateway), registrar el callback `${SARA_PUBLIC_ORIGIN}/auth/callback` para `smartdoc-web` en el portal y establecer `SARA_LOGIN_ENABLED=true`. La integración PKCE se prueba con un servicio simulado; el portal real aún no está conectado. El compose legado mantiene su modo central anterior.

## Qué se mide

| Origen | Eventos |
|---|---|
| Navegador | Apertura de biblioteca, páginas leídas, citas abiertas, cambio de tema/modo de búsqueda y valoración útil/no útil |
| API | Intentos de subir/abrir, buscar, consultar, resumir, reintentar y comparar; estado HTTP y tiempo hasta cabeceras |
| Chat | Finalización o interrupción del stream, resultado y duración |
| Worker | Procesamiento y síntesis completados o fallidos, con duración |

Los eventos contienen identificador seudónimo de sesión temporal o cuenta, UUID del evento, fecha de servidor, tipo predefinido y, cuando corresponde, valor enumerado, duración o estado. No aceptan propiedades arbitrarias ni texto libre. No incluyen contenido, nombre o ID de documento, preguntas, respuestas, URL de búsqueda, IP, user-agent ni fingerprint. Los access logs de Uvicorn están desactivados en el contenedor de producción para no registrar consultas en URLs.

Esto es uso **sin cuenta** y medición **seudónima por navegador o cuenta**, no una garantía de anonimato absoluto ante la infraestructura de red. No se envía telemetría a servicios externos.

Las aperturas de PDF por HTTP Range pueden producir varias peticiones: `document_open` cuenta solicitudes, mientras `page_view` representa renderizados de página, incluidos cambios de tamaño. Los contadores de navegador no son una fuente de auditoría confiable: se pueden manipular desde un cliente. El límite es 120 eventos de navegador por minuto y por sesión; los IDs permiten deduplicar reenvíos. El feedback no se vincula al contenido de la respuesta.

## Control y conservación

En “Sobre este espacio y la medición de uso” se puede desactivar la medición. La preferencia se guarda en el servidor y se aplica también al worker y a eventos que estaban pendientes en la cola. Desactivarla no elimina los eventos ya registrados; caducan con la política general.

- `SARA_ANALYTICS_ENABLED=false`: desactiva la medición global.
- `SARA_ANALYTICS_RETENTION_DAYS=90`: conservación por defecto, configurable entre 1 y 365 días.
- Limpieza al iniciar y diariamente mientras el catálogo está activo. El periodo mostrado por la interfaz proviene del servidor.
- La cola de eventos del servidor es limitada y de mejor esfuerzo: si se llena o falla PostgreSQL, se pueden perder métricas sin fallar la operación documental. No es un registro contable.

## Informe para mejorar el producto

Sólo para operadores con acceso al servidor; no hay endpoint público de estadísticas:

```bash
docker compose --env-file .env.sara -f compose.sara.yml exec library \
  python -m library_service.analytics_report --days 7
```

Devuelve por evento/origen/valor: total, identificadores distintos, errores, media y p95 de duración. No devuelve identificadores de visitas, documentos ni conversaciones. Permite detectar funciones poco utilizadas, pasos con errores, lentitud del procesamiento y utilidad percibida del chat.

## Datos anteriores

El cambio no modifica los UUID, archivos ni permisos del sistema anterior. Su importación a un acceso anónimo requiere una decisión explícita sobre el destino; no debe inferirse un propietario nuevo a partir de un nombre, correo o cookie.
