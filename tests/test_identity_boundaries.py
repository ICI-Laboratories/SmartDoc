import tempfile
import unittest
from pathlib import Path

from frontend.lib.central_identity import (
    InvalidCentralSubject,
    canonical_subject,
    subject_directory,
)
from llm_service.path_scope import SubjectScopeError, paths_for_subject

SUBJECT = "123e4567-e89b-12d3-a456-426614174000"
OTHER_SUBJECT = "123e4567-e89b-12d3-a456-426614174001"


class CentralIdentityTests(unittest.TestCase):
    def test_only_canonical_uuid_is_accepted(self):
        self.assertEqual(canonical_subject(SUBJECT), SUBJECT)
        with self.assertRaises(InvalidCentralSubject):
            canonical_subject("usuario_web_attacker")
        with self.assertRaises(InvalidCentralSubject):
            canonical_subject("{123e4567-e89b-12d3-a456-426614174000}")

    def test_document_paths_are_limited_to_central_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            own = base / SUBJECT / "paper.md"
            own.parent.mkdir()
            own.write_text("own", encoding="utf-8")
            other = base / OTHER_SUBJECT / "paper.md"
            other.parent.mkdir()
            other.write_text("other", encoding="utf-8")

            self.assertEqual(paths_for_subject(base, SUBJECT, [str(own)]), [str(own.resolve())])
            with self.assertRaises(SubjectScopeError):
                paths_for_subject(base, SUBJECT, [str(other)])
            with self.assertRaises(SubjectScopeError):
                paths_for_subject(base, SUBJECT, [str(base / SUBJECT / ".." / OTHER_SUBJECT / "paper.md")])
            self.assertEqual(subject_directory(base, SUBJECT), (base / SUBJECT).resolve())

    def test_runtime_removed_browser_chosen_identity(self):
        root = Path(__file__).resolve().parents[1]
        common = (root / "frontend" / "lib" / "common.py").read_text(encoding="utf-8")
        component = (
            root / "frontend" / "lib" / "browser_session_component" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("X-User-ID", common)
        self.assertNotIn("localStorage", component)


if __name__ == "__main__":
    unittest.main()
