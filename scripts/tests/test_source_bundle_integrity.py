"""Independent, synthetic source-inventory and ZIP validation regressions."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from source_bundle_integrity import (
    IntegrityError, MANIFEST_NAME, extract_verified_zip, make_manifest,
    package_zip, parse_manifest, permitted_path, verify_tree, verify_zip,
)


class SourceBundleIntegrityTests(unittest.TestCase):
    def sample(self, parent: Path) -> Path:
        root = parent / "craft-anything-full-upgraded"
        (root / "src").mkdir(parents=True)
        (root / "README.md").write_text("Build with care\n", encoding="utf-8")
        (root / "src" / "app.rs").write_text("fn main() {}\n", encoding="utf-8")
        manifest = make_manifest(root)
        (root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return root

    @staticmethod
    def package(root: Path, archive: Path, *, skip: str | None = None):
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.name != skip:
                    zf.write(path, arcname=f"{root.name}/{path.relative_to(root).as_posix()}")

    def test_tree_and_zip_roundtrip_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            self.assertEqual(verify_tree(root)["file_count"], 2)
            archive = parent / "source.zip"
            self.package(root, archive)
            self.assertEqual(verify_zip(archive)["status"], "verified")

    def test_renamed_root_zip_and_tree_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            renamed = "craft-anything-full-upgraded-bugfix-deep-gate-inventory-tests-and-doc-drift"
            archive = parent / "branch.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        zf.write(path, arcname=f"{renamed}/{path.relative_to(root).as_posix()}")
            verified = verify_zip(archive)
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(verified["declared_root"], root.name)
            self.assertEqual(verified["source_root"], renamed)
            install = parent / "install"
            install.mkdir()
            receipt = extract_verified_zip(archive, install)
            self.assertEqual(receipt["status"], "extracted")
            installed = install / renamed
            checked = verify_tree(installed)
            self.assertEqual(checked["file_count"], 2)
            self.assertEqual(checked["declared_root"], root.name)
            self.assertEqual(checked["source_root"], renamed)

    def test_zip_with_nested_source_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "nested-root.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        zf.write(path, arcname=f"outer/{root.name}/{path.relative_to(root).as_posix()}")
            with self.assertRaisesRegex(IntegrityError, "source root is invalid"):
                verify_zip(archive)

    def test_package_zip_verifies_the_artifact_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "artifact.zip"
            receipt = package_zip(root, archive)
            self.assertEqual(receipt["status"], "packaged")
            self.assertEqual(receipt["source_root"], root.name)
            self.assertEqual(len(receipt["archive_sha256"]), 64)
            self.assertEqual(verify_zip(archive)["status"], "verified")
            install = parent / "install"
            install.mkdir()
            self.assertEqual(extract_verified_zip(archive, install)["status"], "extracted")
            self.assertTrue((install / root.name / "README.md").is_file())
            self.assertFalse(any(parent.glob(".source-zip-*")))
            with self.assertRaisesRegex(IntegrityError, "Refusing to overwrite"):
                package_zip(root, archive)
            (root / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(IntegrityError, "missing, changed or extra"):
                package_zip(root, parent / "stale.zip")
            self.assertFalse(any(parent.glob(".source-zip-*")))
            with self.assertRaisesRegex(IntegrityError, "outside the source root"):
                package_zip(root, root / "artifact.zip")

    def test_verified_extraction_installs_exact_source_without_running_it(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            install = parent / "install"
            install.mkdir()
            receipt = extract_verified_zip(archive, install)
            self.assertEqual(receipt["status"], "extracted")
            self.assertEqual(receipt["file_count"], 2)
            installed = install / root.name
            self.assertEqual(verify_tree(installed)["file_count"], 2)
            self.assertEqual((installed / "README.md").read_text(encoding="utf-8"), "Build with care\n")
            self.assertFalse(any(install.glob(".verified-source-*")))

    def test_verified_extraction_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            install = parent / "install"
            target = install / root.name
            target.mkdir(parents=True)
            sentinel = target / "keep.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            with self.assertRaisesRegex(IntegrityError, "Refusing to overwrite"):
                extract_verified_zip(archive, install)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")
            self.assertFalse(any(install.glob(".verified-source-*")))

    def test_verified_extraction_does_not_create_output_on_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "tampered.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.write(root / MANIFEST_NAME, f"{root.name}/{MANIFEST_NAME}")
                zf.writestr(f"{root.name}/README.md", "bad")
                zf.write(root / "src" / "app.rs", f"{root.name}/src/app.rs")
            install = parent / "install"
            install.mkdir()
            with self.assertRaises(IntegrityError):
                extract_verified_zip(archive, install)
            self.assertEqual(list(install.iterdir()), [])

    def test_verified_extraction_cleans_private_staging_on_postcheck_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            install = parent / "install"
            install.mkdir()
            with patch("source_bundle_integrity.verify_tree", side_effect=IntegrityError("postcheck rejected")):
                with self.assertRaisesRegex(IntegrityError, "postcheck rejected"):
                    extract_verified_zip(archive, install)
            self.assertEqual(list(install.iterdir()), [])

    def test_verified_extraction_rejects_symlink_destination_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            real_parent = parent / "install"
            real_parent.mkdir()
            link = parent / "install-link"
            try:
                os.symlink(real_parent, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Filesystem cannot create symlinks")
            with self.assertRaisesRegex(IntegrityError, "non-symlink"):
                extract_verified_zip(archive, link)
            self.assertEqual(list(real_parent.iterdir()), [])

    def test_extract_zip_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            install = parent / "install"
            install.mkdir()
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "source_bundle_integrity.py"), "extract-zip",
                 "--archive", str(archive), "--output-parent", str(install)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "extracted")

    def test_rejects_nonportable_names(self):
        for name in ("CON", "NUL.txt", "NUL  .txt", "foo/LPT1.bin", "foo/COM¹.data", "x?y", "a//",
                     "folder/file. ", "folder/name ", "foo/<bar>",
                     "folder/part\x01", "Cafe\u0301.txt", "A/aux.json", "odd\udcff"):
            with self.subTest(name=name):
                self.assertFalse(permitted_path(name))
        self.assertTrue(permitted_path("docs/Café.txt"))

    def test_zip_refuses_case_insensitive_directory_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "collision.zip"
            self.package(root, archive)
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr(f"{root.name}/SRC/else.rs", "fn main() {}")
            with self.assertRaisesRegex(IntegrityError, "component collision"):
                verify_zip(archive)

    def test_zip_refuses_files_used_as_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "nested-file.zip"
            self.package(root, archive)
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr(f"{root.name}/README.md/child", "no")
            with self.assertRaisesRegex(IntegrityError, "parent directory"):
                verify_zip(archive)

    def test_git_checkout_metadata_is_ignored_but_extra_source_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            (root / ".git" / "objects").mkdir(parents=True)
            (root / ".git" / "objects" / "object").write_bytes(b"internal")
            self.assertEqual(verify_tree(root)["status"], "verified")
            (root / "source.rs").write_text("new source", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_tree(root)

    def test_root_cargo_target_is_ignored_but_nested_source_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            (root / "target" / "debug").mkdir(parents=True)
            (root / "target" / "debug" / "craft-anything-cli").write_bytes(b"binary output")
            self.assertEqual(verify_tree(root)["status"], "verified")
            (root / "src" / "target").mkdir()
            (root / "src" / "target" / "unlisted.rs").write_text("unsafe change", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_tree(root)

    def test_the_local_devlog_is_ignored_but_nested_logs_are_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            (root / "log").mkdir()
            (root / "log" / "devlog.md").write_text("local scratch", encoding="utf-8")
            self.assertEqual(verify_tree(root)["status"], "verified")
            (root / "src" / "log").mkdir()
            (root / "src" / "log" / "app.log").write_text("not source", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_tree(root)

    def test_tampered_missing_and_extra_tree_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            source = root / "src" / "app.rs"
            source.write_text("evil code", encoding="utf-8")
            with self.assertRaisesRegex(IntegrityError, "missing, changed or extra"):
                verify_tree(root)
            source.write_text("fn main() {}\n", encoding="utf-8")
            source.unlink()
            with self.assertRaises(IntegrityError):
                verify_tree(root)
            source.write_text("fn main() {}\n", encoding="utf-8")
            (root / "additional.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_tree(root)

    def test_missing_zip_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "missing.zip"
            self.package(root, archive, skip="app.rs")
            with self.assertRaisesRegex(IntegrityError, "missing, changed or extra"):
                verify_zip(archive)

    def test_traversal_and_duplicate_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "unsafe.zip"
            self.package(root, archive)
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr("../outside.txt", "bad")
            with self.assertRaisesRegex(IntegrityError, "Unsafe or duplicate"):
                verify_zip(archive)
            duplicate = parent / "duplicate.zip"
            self.package(root, duplicate)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "a") as zf:
                    zf.writestr(f"{root.name}/README.md", "second copy")
            with self.assertRaisesRegex(IntegrityError, "Unsafe or duplicate"):
                verify_zip(duplicate)

    def test_symlink_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "symlink.zip"
            self.package(root, archive)
            bad = zipfile.ZipInfo(f"{root.name}/link")
            bad.create_system = 3
            bad.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr(bad, "../outside")
            with self.assertRaisesRegex(IntegrityError, "symbolic link"):
                verify_zip(archive)

    def test_external_zip_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "other-folder.zip"
            self.package(root, archive)
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr("unrelated-folder/", "")
            with self.assertRaisesRegex(IntegrityError, "directory outside"):
                verify_zip(archive)

    def test_tree_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            try:
                os.symlink(root / "README.md", root / "link.txt")
            except (OSError, NotImplementedError):
                self.skipTest("Filesystem cannot create symlinks")
            with self.assertRaisesRegex(IntegrityError, "symbolic link"):
                verify_tree(root)

    def test_invalid_manifest_order_and_lengths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            manifest = make_manifest(root)
            manifest["files"].reverse()
            with self.assertRaisesRegex(IntegrityError, "sorted"):
                parse_manifest(json.dumps(manifest).encode("utf-8"))
            manifest["files"].reverse()
            manifest["total_bytes"] += 1
            with self.assertRaisesRegex(IntegrityError, "total"):
                parse_manifest(json.dumps(manifest).encode("utf-8"))

    def test_cli_create_and_verify_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "craft-anything-full-upgraded"
            root.mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            script = str(SCRIPTS / "source_bundle_integrity.py")
            for mode in ("create", "verify-tree"):
                result = subprocess.run(
                    [sys.executable, script, mode, "--root", str(root)],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "verified")


if __name__ == "__main__":
    unittest.main()
