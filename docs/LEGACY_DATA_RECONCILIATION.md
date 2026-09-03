# Reconciliación de espacios anónimos heredados

El esquema anterior generaba en el navegador nombres como `usuario_web_*` y los aceptaba por `X-User-ID`. Esos valores no prueban identidad y por eso **no se enlazan automáticamente** con cuentas centrales.

El corte conserva intactos:

- las carpetas existentes bajo `SMARTREVIEW_BASE`;
- `smartreview_users.db`, si existe;
- PDFs, Markdown, resúmenes y vectores derivados.

El runtime nuevo no consulta la tabla heredada y sólo abre el directorio del UUID central autenticado. No renombre, copie ni elimine espacios antiguos por email, nombre visible, orden de acceso o coincidencias aproximadas.

Para reclamar datos se requiere un manifiesto revisado explícitamente con una fila por operación:

```text
legacy_folder,central_user_uuid,decision,evidence,reviewed_by,reviewed_at
```

`decision` debe ser `copy`, `move`, `leave_unclaimed` o `archive`. Antes de ejecutar cualquier `copy` o `move`, confirme que el UUID exista y esté activo en `auth_services`, que el propietario aprobó la operación y que no haya colisión con archivos del namespace destino. Mantenga backup y bitácora reversible. Este repositorio no incluye una inferencia automática deliberadamente.
