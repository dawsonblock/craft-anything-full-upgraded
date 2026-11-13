"""Release source-artifact verification and receipt regressions."""

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from source_bundle_integrity import (
    IntegrityError, MANIFEST_NAME, make_manifest, package_zip,
)
import verify_release_source


class VerifyReleaseSourceTests(unittest.TestCase):
    def sample(self, parent: Path) -> Path:
        root = parent / "craft-anything-full-upgraded"
        (root / "src").mkdir(parents=True)
        (root / "README.md").write_text("Build with care\n", encoding="utf-8")
        (root / "src" / "app.rs").write_text("fn main() {}\n", encoding="utf-8")
        manifest = make_manifest(root)
        (root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def run_main(self, argv: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = verify_release_source.main(argv)
        return code, json.loads(output.getvalue())

    def test_local_release_archive_writes_a_verified_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            receipt_path = parent / "receipt.json"
            code, receipt = self.run_main([
                "--archive", str(archive), "--report", str(receipt_path),
                "--root", str(root),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["status"], "verified")
            self.assertEqual(receipt["file_count"], 2)
            self.assertEqual(receipt["archive_name"], "release-source.zip")
            self.assertEqual(len(receipt["archive_sha256"]), 64)
            self.assertEqual(len(receipt["manifest_sha256"]), 64)
            self.assertFalse(receipt["root_renamed"])
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")),
                             receipt)

    def test_receipt_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            receipt_path = parent / "receipt.json"
            receipt_path.write_text("keep me\n", encoding="utf-8")
            code, receipt = self.run_main([
                "--archive", str(archive), "--report", str(receipt_path),
                "--root", str(root),
            ])
            self.assertEqual(code, 1)
            self.assertIn("Refusing to overwrite", receipt["error"])
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), "keep me\n")

    def test_receipt_inside_the_source_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            code, receipt = self.run_main([
                "--archive", str(archive), "--report", str(root / "receipt.json"),
                "--root", str(root),
            ])
            self.assertEqual(code, 1)
            self.assertIn("outside the source tree", receipt["error"])

    def test_renamed_release_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "branch.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        zf.write(
                            path,
                            arcname=f"craft-anything-v9.9.9/{path.relative_to(root).as_posix()}",
                        )
            receipt_path = parent / "receipt.json"
            code, receipt = self.run_main([
                "--archive", str(archive), "--report", str(receipt_path),
                "--root", str(root),
            ])
            self.assertEqual(code, 1)
            self.assertIn("declared source root", receipt["error"])
            written = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(written["status"], "error")

    def test_archive_without_a_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "github-source.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("craft-anything-0.6.1/README.md", "no manifest\n")
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
            ])
            self.assertEqual(code, 1)
            self.assertIn("one source integrity manifest", receipt["error"])

    def test_remote_mode_verifies_the_downloaded_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)

            def fake_download(repo, tag, asset, destination):
                target = Path(destination) / "downloaded-source.zip"
                shutil.copyfile(archive, target)
                return target

            receipt_path = parent / "receipt.json"
            with patch.object(verify_release_source, "download_release",
                              side_effect=fake_download):
                code, receipt = self.run_main([
                    "--repo", "storytold/craft-anything", "--tag", "v0.6.1",
                    "--report", str(receipt_path), "--root", str(root),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["status"], "verified")
            self.assertEqual(receipt["release"], {
                "repo": "storytold/craft-anything", "tag": "v0.6.1",
                "asset": "*source*.zip",
            })

    def test_remote_mode_requires_a_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            code, receipt = self.run_main([
                "--repo", "storytold/craft-anything", "--root", str(root),
            ])
            self.assertEqual(code, 1)
            self.assertIn("--tag is required", receipt["error"])

    def test_download_failure_reports_the_cli_message(self):
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.CompletedProcess(
                [], 1, "", "no assets match the file pattern",
            )
            with patch.object(verify_release_source.subprocess, "run",
                              return_value=completed):
                with self.assertRaisesRegex(IntegrityError, "no assets match"):
                    verify_release_source.download_release(
                        "storytold/craft-anything", "v0.6.1", "*source*.zip",
                        Path(temp),
                    )

    def test_download_requires_exactly_one_matching_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            (destination / "one.zip").write_bytes(b"one")
            (destination / "two.zip").write_bytes(b"two")
            completed = subprocess.CompletedProcess([], 0, "", "")
            with patch.object(verify_release_source.subprocess, "run",
                              return_value=completed):
                with self.assertRaisesRegex(IntegrityError, "exactly one source ZIP"):
                    verify_release_source.download_release(
                        "storytold/craft-anything", "v0.6.1", "*source*.zip",
                        destination,
                    )


if __name__ == "__main__":
    unittest.main()
