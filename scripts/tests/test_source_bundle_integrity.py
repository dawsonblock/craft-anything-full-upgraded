"""Independent, synthetic source-inventory and ZIP validation regressions."""

import hashlib
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
    IntegrityError, MANIFEST_NAME, extract_verified_zip, git_tracked, ignored, make_manifest,
    package_zip, parse_manifest, permitted_path, source_records, verify_tree, verify_zip,
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

    @staticmethod
    def git_sample(parent: Path) -> Path:
        """A small work tree whose files are staged, so `git ls-files` lists them."""
        root = parent / "craft-anything-full-upgraded"
        (root / "src").mkdir(parents=True)
        (root / "README.md").write_text("Build with care\n", encoding="utf-8")
        (root / "src" / "app.rs").write_text("fn main() {}\n", encoding="utf-8")
        for command in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-C", str(root), *command],
                           capture_output=True, text=True, timeout=30, check=True)
        return root

    @staticmethod
    def run_cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "source_bundle_integrity.py"), *args],
            capture_output=True, text=True, timeout=60, check=False,
        )

    def write_ruff_cache(self, root: Path) -> None:
        """Reproduce the cache `ruff` leaves behind, including its self-ignoring .gitignore."""
        cache = root / ".ruff_cache"
        (cache / "0.16.0").mkdir(parents=True)
        (cache / ".gitignore").write_text("*\n", encoding="utf-8")
        (cache / "CACHEDIR.TAG").write_text(
            "Signature: 8a477f597d28d172789f06886806bc55\n", encoding="utf-8")
        (cache / "0.16.0" / "9885096104526973474").write_bytes(b"cache")

    def test_tree_and_zip_roundtrip_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            self.assertEqual(verify_tree(root)["file_count"], 2)
            archive = parent / "source.zip"
            self.package(root, archive)
            self.assertEqual(verify_zip(archive)["status"], "verified")

    def test_root_policy_is_visible_in_every_result(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            self.assertEqual(verify_tree(root)["root_policy"], "compatible")
            self.assertEqual(
                verify_tree(root, require_declared_root=True)["root_policy"],
                "strict",
            )
            archive = parent / "source.zip"
            self.package(root, archive)
            self.assertEqual(verify_zip(archive)["root_policy"], "compatible")
            self.assertEqual(
                verify_zip(archive, require_declared_root=True)["root_policy"],
                "strict",
            )
            artifact = parent / "artifact.zip"
            self.assertEqual(package_zip(root, artifact)["root_policy"], "strict")
            install = parent / "install"
            install.mkdir()
            self.assertEqual(
                extract_verified_zip(artifact, install)["root_policy"],
                "compatible",
            )

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
            self.assertTrue(verified["root_renamed"])
            with self.assertRaisesRegex(IntegrityError, "declared source root"):
                verify_zip(archive, require_declared_root=True)
            install = parent / "install"
            install.mkdir()
            receipt = extract_verified_zip(archive, install)
            self.assertEqual(receipt["status"], "extracted")
            self.assertTrue(receipt["root_renamed"])
            installed = install / renamed
            checked = verify_tree(installed)
            self.assertEqual(checked["file_count"], 2)
            self.assertEqual(checked["declared_root"], root.name)
            self.assertEqual(checked["source_root"], renamed)
            self.assertTrue(checked["root_renamed"])
            with self.assertRaisesRegex(IntegrityError, "differs from manifest root"):
                verify_tree(installed, require_declared_root=True)
            strict_install = parent / "strict-install"
            strict_install.mkdir()
            with self.assertRaisesRegex(IntegrityError, "declared source root"):
                extract_verified_zip(archive, strict_install, require_declared_root=True)
            self.assertEqual(list(strict_install.iterdir()), [])

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
            self.assertFalse(receipt["root_renamed"])
            self.assertEqual(len(receipt["archive_sha256"]), 64)
            self.assertEqual(verify_zip(archive)["status"], "verified")
            self.assertEqual(verify_zip(archive, require_declared_root=True)["status"], "verified")
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

    def test_package_zip_normalizes_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            first = parent / "first.zip"
            package_zip(root, first)
            first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
            os.utime(root / "README.md", (1_600_000_000, 1_600_000_000))
            os.utime(root / "src" / "app.rs", (1_500_000_000, 1_500_000_000))
            second = parent / "second.zip"
            package_zip(root, second)
            self.assertEqual(hashlib.sha256(second.read_bytes()).hexdigest(),
                             first_digest)

    def test_extract_verified_zip_accepts_an_open_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "source.zip"
            self.package(root, archive)
            install = parent / "install"
            install.mkdir()
            with archive.open("rb") as stream:
                receipt = extract_verified_zip(stream, install)
            self.assertEqual(receipt["status"], "extracted")
            self.assertTrue((install / root.name / "README.md").is_file())

    def test_package_zip_runs_a_full_extraction_check_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "rejected.zip"
            with patch("source_bundle_integrity.extract_verified_zip",
                       side_effect=IntegrityError("postcheck rejected")):
                with self.assertRaisesRegex(IntegrityError, "postcheck rejected"):
                    package_zip(root, archive)
            self.assertFalse(archive.exists())
            self.assertFalse(any(parent.glob(".source-zip-*")))
            self.assertFalse(any(parent.glob(".package-check-*")))

    def test_package_zip_refuses_a_destination_created_during_packaging(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "artifact.zip"
            sentinel = b"another writer\n"

            def create_destination(*_args, **_kwargs):
                archive.write_bytes(sentinel)
                return {"status": "extracted"}

            with patch("source_bundle_integrity.extract_verified_zip",
                       side_effect=create_destination):
                with self.assertRaisesRegex(IntegrityError, "Refusing to overwrite"):
                    package_zip(root, archive)
            self.assertEqual(archive.read_bytes(), sentinel)
            self.assertFalse(any(parent.glob(".source-zip-*")))
            self.assertFalse(any(parent.glob(".package-check-*")))

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

    def test_cli_strict_root_flag_rejects_a_renamed_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            renamed = "craft-anything-full-upgraded-bugfix-example"
            archive = parent / "branch.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        zf.write(path, arcname=f"{renamed}/{path.relative_to(root).as_posix()}")
            script = str(SCRIPTS / "source_bundle_integrity.py")
            flexible = subprocess.run(
                [sys.executable, script, "verify-zip", "--archive", str(archive)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(flexible.returncode, 0, flexible.stdout + flexible.stderr)
            strict = subprocess.run(
                [sys.executable, script, "verify-zip", "--archive", str(archive),
                 "--require-declared-root"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)
            self.assertIn("declared source root", json.loads(strict.stdout)["error"])

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

    def test_tool_caches_do_not_change_the_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            before = make_manifest(root)
            self.write_ruff_cache(root)
            (root / "scripts" / "__pycache__").mkdir(parents=True)
            (root / "scripts" / "__pycache__" / "tool.cpython-312.pyc").write_bytes(b"bytecode")
            self.assertEqual(make_manifest(root), before)
            self.assertEqual(verify_tree(root)["status"], "verified")
            (root / "src" / "app.rs").write_text("fn main() { evil(); }\n", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_tree(root)

    def test_a_manifest_that_names_a_cache_file_still_verifies(self):
        """An archive recorded before caches were excluded must still verify and extract."""
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = parent / "craft-anything-full-upgraded"
            (root / ".ruff_cache").mkdir(parents=True)
            (root / "README.md").write_text("hello\n", encoding="utf-8")
            (root / ".ruff_cache" / "CACHEDIR.TAG").write_text("cache\n", encoding="utf-8")
            manifest = {
                "schema": 1, "root": root.name, "algorithm": "sha256",
                "file_count": 2, "total_bytes": 12,
                "files": [
                    {"path": ".ruff_cache/CACHEDIR.TAG", "bytes": 6,
                     "sha256": hashlib.sha256(b"cache\n").hexdigest()},
                    {"path": "README.md", "bytes": 6,
                     "sha256": hashlib.sha256(b"hello\n").hexdigest()},
                ],
            }
            (root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(verify_tree(root)["file_count"], 2)
            archive = parent / "legacy.zip"
            self.package(root, archive)
            install = parent / "install"
            install.mkdir()
            self.assertEqual(extract_verified_zip(archive, install)["file_count"], 2)

    def test_create_in_a_work_tree_records_only_tracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            self.write_ruff_cache(root)
            result = self.run_cli("create", "--root", str(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["file_source"], "git")
            self.assertEqual(json.loads(result.stdout)["file_count"], 2)
            manifest = parse_manifest((root / MANIFEST_NAME).read_bytes())
            self.assertEqual([row["path"] for row in manifest["files"]],
                             ["README.md", "src/app.rs"])
            verified = self.run_cli("verify-tree", "--root", str(root), "--require-declared-root")
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_tracked_mode_refuses_an_untracked_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            (root / "stray.txt").write_text("not committed", encoding="utf-8")
            result = self.run_cli("create", "--root", str(root))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Git does not track", result.stdout)
            self.assertIn("stray.txt", result.stdout)
            self.assertFalse((root / MANIFEST_NAME).exists())

    def test_local_scratch_does_not_block_regenerating_the_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            (root / "plan").mkdir()
            (root / "plan" / "notes.md").write_text("private", encoding="utf-8")
            (root / ".env").write_text("TOKEN=local\n", encoding="utf-8")
            (root / "local.pem").write_text("secret\n", encoding="utf-8")
            (root / "screenshots").mkdir()
            (root / "screenshots" / "shot.png").write_bytes(b"png")
            result = self.run_cli("create", "--root", str(root))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = parse_manifest((root / MANIFEST_NAME).read_bytes())
            self.assertEqual([row["path"] for row in manifest["files"]],
                             ["README.md", "src/app.rs"])

    def test_verification_reports_what_it_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            self.write_ruff_cache(root)
            (root / "target").mkdir()
            (root / "target" / "debug").write_bytes(b"binary")
            report = verify_tree(root)
            self.assertEqual(report["excluded"], [".ruff_cache/", "target/"])
            self.assertEqual(report["excluded_count"], 2)
            with self.assertRaisesRegex(IntegrityError, "inventory excludes"):
                verify_tree(root, require_exact=True)

    def test_create_refuses_a_subdirectory_of_a_work_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            result = self.run_cli("create", "--root", str(root / "src"))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("repository root", result.stdout)
            self.assertFalse((root / "src" / MANIFEST_NAME).exists())

    def test_create_allows_an_extraction_inside_an_unrelated_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self.git_sample(Path(temp))
            extracted = repo / "downloads" / "craft-anything-full-upgraded"
            (extracted / "src").mkdir(parents=True)
            (extracted / "src" / "app.rs").write_text("fn main() {}\n", encoding="utf-8")
            result = self.run_cli("create", "--root", str(extracted))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["file_source"], "filesystem")

    def test_the_inventory_ignores_every_simple_gitignore_entry(self):
        patterns = []
        for line in (SCRIPTS.parent / ".gitignore").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or any(ch in line for ch in "*?["):
                continue
            patterns.append(line.lstrip("/").rstrip("/"))
        self.assertTrue(patterns)
        for name in patterns:
            self.assertTrue(ignored(Path(name)), f".gitignore entry is not ignored: {name}")

    def test_no_tracked_walks_the_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            result = self.run_cli("create", "--root", str(root), "--no-tracked")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["file_source"], "filesystem")

    def test_tracked_mode_reports_a_missing_tracked_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.git_sample(Path(temp))
            (root / "src" / "app.rs").unlink()
            with self.assertRaisesRegex(IntegrityError, "Tracked file is missing"):
                source_records(root, git_tracked(root))

    def test_a_damaged_git_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.sample(Path(temp))
            (root / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
            with self.assertRaisesRegex(IntegrityError, "Git could not"):
                git_tracked(root)

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
