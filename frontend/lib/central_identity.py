from pathlib import Path
from uuid import UUID


class InvalidCentralSubject(ValueError):
    """A value is not a canonical auth_services subject UUID."""


def canonical_subject(raw_subject: object) -> str:
    try:
        parsed = UUID(str(raw_subject))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidCentralSubject("subject invalido") from exc
    canonical = str(parsed)
    if str(raw_subject).lower() != canonical:
        raise InvalidCentralSubject("subject no canonico")
    return canonical


def subject_directory(base_dir: Path, raw_subject: object) -> Path:
    base = base_dir.resolve()
    candidate = (base / canonical_subject(raw_subject)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise InvalidCentralSubject("namespace fuera del directorio base") from exc
    return candidate
