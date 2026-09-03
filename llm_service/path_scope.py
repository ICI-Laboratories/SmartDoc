from collections.abc import Iterable
from pathlib import Path
from uuid import UUID


class SubjectScopeError(ValueError):
    """A subject or document path crosses the authenticated namespace boundary."""


def canonical_subject(raw_subject: object) -> str:
    try:
        parsed = UUID(str(raw_subject))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SubjectScopeError("subject invalido") from exc
    canonical = str(parsed)
    if str(raw_subject).lower() != canonical:
        raise SubjectScopeError("subject no canonico")
    return canonical


def paths_for_subject(base_dir: Path, raw_subject: object, paths: Iterable[str]) -> list[str]:
    base = base_dir.resolve()
    subject_root = (base / canonical_subject(raw_subject)).resolve()
    try:
        subject_root.relative_to(base)
    except ValueError as exc:
        raise SubjectScopeError("namespace central fuera del directorio base") from exc
    safe_paths: list[str] = []
    for supplied in paths:
        candidate = Path(supplied)
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(subject_root)
        except ValueError as exc:
            raise SubjectScopeError("ruta fuera del namespace central") from exc
        safe_paths.append(str(resolved))
    return safe_paths
