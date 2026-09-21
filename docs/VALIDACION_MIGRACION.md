# Validación actualizada: acceso sin cuenta

21 de septiembre de 2026. El despliegue moderno ofrece acceso temporal y soporte de cuentas centrales, desactivadas por ahora.

- 24 pruebas Python: caducidad y eliminación del espacio temporal, preservación de bibliotecas anteriores y preferencias de cuentas, además de sesiones anónimas persistentes, hash de secretos, expiración, esquema cerrado de eventos, deduplicación, opt-out, limpieza por retención, ausencia de texto de consultas en métricas y límites de envío.
- 10 pruebas Rust: integración PKCE en modo mixto y legado, selector sin creación automática, además de acceso anónimo, cookie HttpOnly, rechazo de identidad elegida por el navegador, CSRF, bloqueo de rutas internas/legadas y continuidad tras recrear el gateway.
- 3 escenarios Playwright: selector escritorio/móvil con login desactivado, entrada y salida del espacio temporal, preferencia de medición persistente, subida, worker, búsqueda, visor, escritorio/móvil y temas.
- SvelteKit compila sin errores y `svelte-check` no reporta advertencias.
- Prueba del despliegue completo en `http://127.0.0.1:8043`: el selector no crea una sesión automáticamente; crear requiere CSRF. Dos navegadores reciben espacios distintos; un PDF sintético es accesible sólo por su propietario. «Terminar y borrar espacio» elimina original, registro y trabajo, revoca la cookie y vuelve al selector. Preferencia de medición persistente y login desactivado con respuesta 503. El PDF sintético se eliminó mediante ese flujo.

La validación previa de OCR real con PDF mixto y Tesseract sigue siendo aplicable. El portal central ya no es una dependencia del producto moderno. No se han migrado datos reales ni medido carga de 10 usuarios concurrentes. La calidad y rendimiento de modelos reales en `cite-server` siguen pendientes.

Consulta [ACCESO_ANONIMO_Y_METRICAS.md](ACCESO_ANONIMO_Y_METRICAS.md) para alcance de los eventos y [MIGRACION_SARA.md](MIGRACION_SARA.md) para reproducir las pruebas.
