# SARA DocReader

Biblioteca documental web para el laboratorio, con interfaz SvelteKit/TypeScript e identidad visual SARA. Permite subir y abrir PDF sin esperar a la IA, buscar en su contenido y consultar un documento con fuentes.

## Tecnología

SvelteKit + PDF.js para la interfaz; gateway Rust/Axum con acceso temporal y soporte de cuentas centrales; catálogo FastAPI, PostgreSQL + pgvector y worker Python independiente para extracción/OCR. La búsqueda textual funciona sin un modelo de lenguaje.

## Ejecutar

```bash
cp .env.sara.example .env.sara
# Configurar la contraseña de PostgreSQL.
docker compose --env-file .env.sara -f compose.sara.yml up --build -d
```

Abrir `http://127.0.0.1:8043`. El puerto queda en loopback para conectar el proxy HTTPS del servidor. El selector permite crear un espacio temporal sin cuenta (24 horas por defecto). El acceso con cuenta central queda preparado y desactivado hasta conectar el portal; el modo temporal no requiere `auth_services`.

- [Acceso anónimo y métricas de producto](docs/ACCESO_ANONIMO_Y_METRICAS.md)
- [Configuración del gateway de inferencia y activación por fases](docs/gateway-migration.md)
- [Despliegue, migración de datos, pruebas y pendientes](docs/MIGRACION_SARA.md)
- [Métodos investigados y decisiones técnicas](docs/METODOS_DOCUMENTALES.md)
- [Diagnóstico inicial](docs/PROPUESTA_MODERNIZACION.md)
- [Documentación del sistema anterior](docs/README_LEGACY.md)

La nueva base funcional convive con el código anterior durante la transición. No se han migrado datos reales ni desplegado en producción. Incluye síntesis y categoría sugerida bajo demanda, chat multidocumento y matriz de similitud; requieren modelos locales configurados. Consultar límites y validación pendiente en la guía de migración.
