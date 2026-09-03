"""Legacy batch entrypoint intentionally disabled after the central-auth cutover."""


def main() -> None:
    raise SystemExit(
        "La ingesta batch anonima esta deshabilitada. Usa la interfaz autenticada de "
        "SmartDoc; reactiva este flujo unicamente con workload identity emitida por "
        "auth_services. Consulta docs/AUTH_CUTOVER.md."
    )


if __name__ == "__main__":
    main()
