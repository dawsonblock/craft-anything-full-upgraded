"""Release draft selection regressions."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import release_draft


COMMIT = "a" * 40
OTHER = "b" * 40


def release(identifier: int, tag: str, *, draft: bool = True, target: str = "release") -> dict:
    return {"id": identifier, "tag_name": tag, "draft": draft, "target_commitish": target}


class ReleaseDraftTests(unittest.TestCase):
    def test_version_forms_are_normalized(self):
        self.assertEqual(release_draft.normalize_version("0.4.0"), "0.4.0")
        self.assertEqual(release_draft.normalize_version("v0.4.0"), "0.4.0")
        self.assertEqual(release_draft.normalize_version(" 0.2.0-rc.1 "), "0.2.0-rc.1")
        self.assertEqual(release_draft.normalize_version("1.2.3+build.7"), "1.2.3+build.7")

    def test_a_non_version_is_refused(self):
        for value in ("", "latest", "v1", "1.2", "1.2.3 4", "../1.2.3"):
            with self.subTest(value=value):
                with self.assertRaises(release_draft.DraftError):
                    release_draft.normalize_version(value)

    def test_the_workspace_version_names_the_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Cargo.toml").write_text(
                '[workspace.package]\nversion = "0.4.0"\n', encoding="utf-8"
            )
            self.assertEqual(release_draft.expected_tag(root), "v0.4.0")
            self.assertEqual(release_draft.expected_tag(root, "0.2.0-rc.1"), "v0.2.0-rc.1")
            self.assertEqual(release_draft.expected_tag(root, "v0.5.0"), "v0.5.0")

    def test_a_manifest_without_a_version_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
            with self.assertRaises(release_draft.DraftError):
                release_draft.expected_tag(root)

    def test_the_one_draft_with_the_tag_is_selected(self):
        chosen = release_draft.select_draft(
            [release(1, "v0.3.0", draft=False), release(2, "v0.4.0"), release(3, "v0.5.0")],
            "v0.4.0",
            COMMIT,
        )
        self.assertEqual(chosen["id"], 2)
        self.assertEqual(chosen["tag_name"], "v0.4.0")

    def test_no_draft_with_the_tag_is_refused(self):
        with self.assertRaises(release_draft.DraftError):
            release_draft.select_draft([release(1, "v0.3.0")], "v0.4.0")

    def test_two_drafts_with_the_tag_are_refused(self):
        with self.assertRaises(release_draft.DraftError):
            release_draft.select_draft([release(1, "v0.4.0"), release(2, "v0.4.0")], "v0.4.0")

    def test_a_published_release_is_not_a_draft(self):
        with self.assertRaises(release_draft.DraftError):
            release_draft.select_draft([release(1, "v0.4.0", draft=False)], "v0.4.0")

    def test_slurped_pages_are_flattened(self):
        pages = [[release(1, "v0.3.0")], [release(2, "v0.4.0")]]
        chosen = release_draft.select_draft(pages, "v0.4.0")
        self.assertEqual(chosen["id"], 2)

    def test_a_draft_targeting_another_commit_is_refused(self):
        with self.assertRaises(release_draft.DraftError):
            release_draft.select_draft([release(1, "v0.4.0", target=OTHER)], "v0.4.0", COMMIT)

    def test_a_draft_targeting_the_release_commit_is_accepted(self):
        chosen = release_draft.select_draft(
            [release(1, "v0.4.0", target=COMMIT.upper())], "v0.4.0", COMMIT
        )
        self.assertEqual(chosen["id"], 1)

    def test_a_branch_target_is_not_a_commit(self):
        chosen = release_draft.select_draft([release(1, "v0.4.0", target="release")], "v0.4.0", COMMIT)
        self.assertEqual(chosen["id"], 1)

    def test_a_malformed_commit_is_refused(self):
        with self.assertRaises(release_draft.DraftError):
            release_draft.select_draft([release(1, "v0.4.0")], "v0.4.0", "not-a-commit")

    def test_a_release_without_a_usable_id_is_refused(self):
        for identifier in (0, -1, "2", None):
            with self.subTest(identifier=identifier):
                with self.assertRaises(release_draft.DraftError):
                    release_draft.select_draft([release(identifier, "v0.4.0")], "v0.4.0")

    def run_main(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = release_draft.main(argv)
        return code, output.getvalue().strip()

    def test_the_cli_prints_the_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Cargo.toml").write_text(
                '[workspace.package]\nversion = "0.4.0"\n', encoding="utf-8"
            )
            code, output = self.run_main(["tag", "--root", str(root)])
        self.assertEqual((code, output), (0, "v0.4.0"))

    def test_the_cli_prints_the_selected_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            listing = Path(temp) / "releases.json"
            listing.write_text(json.dumps([release(7, "v0.4.0")]), encoding="utf-8")
            code, output = self.run_main(
                ["select", "--releases", str(listing), "--tag", "v0.4.0", "--commit", COMMIT]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["id"], 7)

    def test_the_cli_reports_an_error_as_json(self):
        with tempfile.TemporaryDirectory() as temp:
            listing = Path(temp) / "releases.json"
            listing.write_text(json.dumps([release(7, "v0.3.0")]), encoding="utf-8")
            code, output = self.run_main(["select", "--releases", str(listing), "--tag", "v0.4.0"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "error")


if __name__ == "__main__":
    unittest.main()
