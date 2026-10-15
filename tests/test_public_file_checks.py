import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_public_files", Path(__file__).parents[1] / "tools" / "check_public_files.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicFileChecksTest(unittest.TestCase):
    def test_private_paths_are_rejected(self):
        errors = CHECK.validate_paths(["var/report.md", ".people-workbench/state.sqlite3", "docs/guide.md"])
        self.assertEqual(len(errors), 3)

    def test_example_environment_file_is_allowed_but_other_env_files_are_not(self):
        self.assertEqual(CHECK.validate_paths([".env.example", ".env.docker.example", "docs/guide.md"]), [])
        rejected = [".env", ".env.production", ".env.docker", ".env.docker.example.private"]
        errors = CHECK.validate_paths(rejected)
        self.assertEqual(len(errors), len(rejected))
        for name, error in zip(rejected, errors):
            self.assertIn(name, error)

    def test_only_readme_and_docs_markdown_are_scanned(self):
        self.assertTrue(CHECK.is_documentation(Path("README.md")))
        self.assertTrue(CHECK.is_documentation(Path("docs/nested/guide.md")))
        self.assertFalse(CHECK.is_documentation(Path("web/index.html")))
        self.assertFalse(CHECK.is_documentation(Path("tools/demo.md")))

    def test_local_markdown_and_html_links(self):
        found = list(CHECK.links("[guide](guide%20one.md#start) <img src='images/example.png'> https://example.test"))
        self.assertEqual(found, ["guide%20one.md#start", "images/example.png"])

    def test_percent_encoded_local_link_is_resolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("[guide](guide%20one.md)", encoding="utf-8")
            (root / "guide one.md").write_text("safe", encoding="utf-8")
            old_root = CHECK.ROOT
            try:
                CHECK.ROOT = root
                errors = CHECK.validate_links(["README.md"])
            finally:
                CHECK.ROOT = old_root
        self.assertEqual(errors, [])

    def test_missing_and_escaping_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("[missing](missing.md) [escape](../outside.md)", encoding="utf-8")
            old_root = CHECK.ROOT
            try:
                CHECK.ROOT = root
                errors = CHECK.validate_links(["README.md"])
            finally:
                CHECK.ROOT = old_root
        self.assertTrue(any("missing local link" in error for error in errors))
        self.assertTrue(any("escapes repository" in error for error in errors))

    def test_unreadable_document_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_root = CHECK.ROOT
            try:
                CHECK.ROOT = root
                errors = CHECK.validate_links(["docs/missing.md"])
            finally:
                CHECK.ROOT = old_root
        self.assertTrue(any("cannot read tracked documentation" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
