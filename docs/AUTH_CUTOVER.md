# SmartDoc: corte a identidad central

SmartDoc ya no crea identidades humanas ni acepta un identificador elegido por el cliente. `auth_services` es la única autoridad de credenciales, sesiones y UUID humanos.

## Flujo web

1. Streamlit muestra el enlace `GET /auth/login` del gateway.
2. El gateway crea `state` y un verificador PKCE aleatorios. Ambos quedan ligados a cookies temporales `HttpOnly`, y el navegador se redirige a `${SMARTDOC_AUTH_PORTAL_URL}/authorize` con `client_id=smartdoc-web` y `code_challenge_method=S256`.
3. `auth_services` regresa únicamente un código de un solo uso a la URL exacta `SMARTDOC_AUTH_CALLBACK_URL`.
4. El gateway valida `state`, canjea el código en el back channel y guarda los tokens centrales sólo en memoria del servidor. El navegador recibe una cookie de sesión opaca `HttpOnly`; ni tokens ni UUID aparecen en URL o `localStorage`.
5. `GET /session/me` y cada petición de producto validan el access token mediante `GET /auth/introspect` con el audience fijo `smartdoc`. Headers cliente como `X-User-ID`, `X-Resource-Audience`, `Authorization` o `X-SmartDoc-Subject` se eliminan antes del proxy.
6. Las mutaciones requieren cookie y header CSRF coincidentes además de un `Origin` exacto. El refresh se serializa por sesión y el logout revoca el refresh token central y elimina la sesión local.

El UUID central canónico es el nuevo namespace de archivos: `${SMARTREVIEW_BASE}/<central-uuid>/...`.

## Registro requerido en auth_services

El operador de `auth_services` debe registrar exactamente el cliente y callback del despliegue. Para Docker local:

```text
client_id: smartdoc-web
audience: smartdoc
redirect_uri: http://localhost:8080/auth/callback
```

Para producción se debe usar HTTPS, cookies `Secure=true` y URLs exactas. Si gateway y Streamlit están en subdominios hermanos, configure un `SMARTDOC_COOKIE_DOMAIN` mínimo que ambos compartan. No use dominios de cookie globales innecesarios.

## Fronteras

- Sólo gateway y Streamlit se publican en Docker. Processor y LLM permanecen en la red interna.
- Streamlit reenvía al gateway exclusivamente las dos cookies SmartDoc vistas por el servidor. Nunca expone el token central al JavaScript.
- `batch_ingest.py` quedó deshabilitado: un proceso batch no debe apropiarse de una sesión humana ni reintroducir credenciales fijas. Se debe reactivar sólo con workload identity/service principal emitido por `auth_services`.
- Las transacciones OAuth y sesiones opacas viven en memoria del gateway. El despliegue actual es de una sola réplica; antes de escalar horizontalmente hace falta un almacén compartido con TTL y operaciones atómicas (o afinidad estricta tanto para login/callback como para toda la sesión). Reiniciar el gateway cierra sus sesiones locales sin afectar los datos de usuario.
