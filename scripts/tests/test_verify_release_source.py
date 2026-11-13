"""Release source-artifact verification and receipt regressions."""

import hashlib
import io
import json
import os
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
WORKFLOWS = SCRIPTS.parent / ".github" / "workflows"
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

    def test_identity_validation_rejects_shell_syntax(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            with patch.object(verify_release_source.subprocess, "run") as run:
                for repo, tag, asset in (
                    ("owner/repo; rm -rf /", "v1", "*source*.zip"),
                    ("owner/repo", "v1$(touch pwned)", "*source*.zip"),
                    ("owner/repo", "v1", "*; touch pwned"),
                    ("owner/repo/extra", "v1", "*source*.zip"),
                    ("owner/repo", "v1\nsecond=value", "*source*.zip"),
                ):
                    with self.assertRaises(IntegrityError):
                        verify_release_source.download_release(
                            repo, tag, asset, destination,
                        )
                run.assert_not_called()

    def second_valid_archive(self, parent: Path) -> Path:
        other_parent = parent / "other"
        other_parent.mkdir()
        other_root = self.sample(other_parent)
        (other_root / "README.md").write_text("Different but valid\n", encoding="utf-8")
        (other_root / MANIFEST_NAME).write_text(
            json.dumps(make_manifest(other_root)), encoding="utf-8",
        )
        replacement = parent / "replacement.zip"
        package_zip(other_root, replacement)
        return replacement

    def test_receipt_binds_the_bytes_that_were_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            original = archive.read_bytes()

            tampered = parent / "tampered.zip"
            manifest = (root / MANIFEST_NAME).read_bytes()
            with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f"{root.name}/{MANIFEST_NAME}", manifest)
                zf.writestr(f"{root.name}/README.md", "tampered\n")
                zf.writestr(f"{root.name}/src/app.rs", "fn main() {}\n")

            real_verify = verify_release_source.verify_zip

            def replace_then_verify(target, **kwargs):
                os.replace(tampered, archive)
                if hasattr(target, "seek"):
                    target.seek(0)
                return real_verify(target, **kwargs)

            with patch.object(verify_release_source, "verify_zip",
                              side_effect=replace_then_verify):
                receipt = verify_release_source.verify_release_archive(archive)
            self.assertEqual(receipt["status"], "verified")
            self.assertEqual(receipt["archive_bytes"], len(original))
            self.assertEqual(receipt["archive_sha256"],
                             hashlib.sha256(original).hexdigest())

    def test_expected_archive_digest_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
                "--expect-archive-sha256", digest,
            ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["status"], "verified")
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
                "--expect-archive-sha256", "0" * 64,
            ])
            self.assertEqual(code, 1)
            self.assertIn("not the expected", receipt["error"])

    def test_local_commit_is_recorded_as_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
                "--commit", "a" * 40,
            ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["source"], {
                "commit": "a" * 40, "verified": False,
            })

    def test_provenance_binds_the_archive_and_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            commit = "a" * 40
            provenance = parent / "preflight.json"
            provenance.write_text(json.dumps({
                "schema": 1, "status": "verified", "archive_sha256": digest,
                "source": {"commit": commit, "verified": False},
            }), encoding="utf-8")

            def fake_download(repo, tag, asset, destination):
                target = Path(destination) / "downloaded-source.zip"
                shutil.copyfile(archive, target)
                return target

            with patch.object(verify_release_source, "download_release",
                              side_effect=fake_download), patch.object(
                    verify_release_source, "resolve_release_commit",
                    return_value=commit):
                code, receipt = self.run_main([
                    "--repo", "storytold/craft-anything", "--tag", "v1.0.0",
                    "--provenance", str(provenance), "--root", str(root),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["source"], {
                "commit": commit, "verified": True,
            })
            self.assertEqual(receipt["provenance"]["receipt"], "preflight.json")
            self.assertEqual(receipt["provenance"]["archive_sha256"], digest)

    def test_provenance_rejects_a_different_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            provenance = parent / "preflight.json"
            provenance.write_text(json.dumps({
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "source": {"commit": "a" * 40},
            }), encoding="utf-8")

            def fake_download(repo, tag, asset, destination):
                target = Path(destination) / "downloaded-source.zip"
                shutil.copyfile(archive, target)
                return target

            with patch.object(verify_release_source, "download_release",
                              side_effect=fake_download), patch.object(
                    verify_release_source, "resolve_release_commit",
                    return_value="b" * 40):
                code, receipt = self.run_main([
                    "--repo", "storytold/craft-anything", "--tag", "v1.0.0",
                    "--provenance", str(provenance), "--root", str(root),
                ])
            self.assertEqual(code, 1)
            self.assertIn("not the expected commit", receipt["error"])

    def test_provenance_rejects_a_different_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            provenance = parent / "preflight.json"
            provenance.write_text(json.dumps({
                "archive_sha256": "0" * 64, "source": {"commit": "a" * 40},
            }), encoding="utf-8")

            def fake_download(repo, tag, asset, destination):
                target = Path(destination) / "downloaded-source.zip"
                shutil.copyfile(archive, target)
                return target

            with patch.object(verify_release_source, "download_release",
                              side_effect=fake_download), patch.object(
                    verify_release_source, "resolve_release_commit",
                    return_value="a" * 40):
                code, receipt = self.run_main([
                    "--repo", "storytold/craft-anything", "--tag", "v1.0.0",
                    "--provenance", str(provenance), "--root", str(root),
                ])
            self.assertEqual(code, 1)
            self.assertIn("not the expected", receipt["error"])

    def test_commit_must_match_the_release_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)

            def fake_download(repo, tag, asset, destination):
                target = Path(destination) / "downloaded-source.zip"
                shutil.copyfile(archive, target)
                return target

            with patch.object(verify_release_source, "download_release",
                              side_effect=fake_download), patch.object(
                    verify_release_source, "resolve_release_commit",
                    return_value="b" * 40):
                code, receipt = self.run_main([
                    "--repo", "storytold/craft-anything", "--tag", "v1.0.0",
                    "--commit", "a" * 40, "--root", str(root),
                ])
            self.assertEqual(code, 1)
            self.assertIn("not the expected commit", receipt["error"])

    def test_resolve_release_commit_reads_the_github_response(self):
        completed = subprocess.CompletedProcess(
            [], 0, json.dumps({"sha": "a" * 40}), "",
        )
        with patch.object(verify_release_source, "run_gh",
                          return_value=completed):
            self.assertEqual(
                verify_release_source.resolve_release_commit(
                    "storytold/craft-anything", "v1.0.0",
                ),
                "a" * 40,
            )
        completed = subprocess.CompletedProcess([], 0, "not json", "")
        with patch.object(verify_release_source, "run_gh",
                          return_value=completed):
            with self.assertRaisesRegex(IntegrityError, "invalid JSON"):
                verify_release_source.resolve_release_commit(
                    "storytold/craft-anything", "v1.0.0",
                )

    def test_attestation_must_cover_the_verified_bytes(self):
        digest = "a" * 64
        payload = [{
            "verificationResult": {
                "statement": {
                    "subject": [{"name": "source.zip", "digest": {"sha256": digest}}],
                },
            },
        }]
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with patch.object(verify_release_source, "run_gh",
                          return_value=completed) as run:
            result = verify_release_source.verify_attestation(
                Path("artifact.zip"), "storytold/craft-anything",
                commit="b" * 40, expected_sha256=digest,
                signer_workflow="storytold/craft-anything/.github/workflows/fork-release-source-preflight.yml",
            )
        self.assertTrue(result["verified"])
        command = run.call_args.args[0]
        self.assertIn("--source-digest", command)
        self.assertIn("--signer-workflow", command)
        with patch.object(verify_release_source, "run_gh",
                          return_value=completed):
            with self.assertRaisesRegex(IntegrityError, "does not cover"):
                verify_release_source.verify_attestation(
                    Path("artifact.zip"), "storytold/craft-anything",
                    commit="b" * 40, expected_sha256="c" * 64,
                    signer_workflow=None,
                )
        empty = subprocess.CompletedProcess([], 0, json.dumps([{}]), "")
        with patch.object(verify_release_source, "run_gh", return_value=empty):
            with self.assertRaisesRegex(IntegrityError, "no subject digest"):
                verify_release_source.verify_attestation(
                    Path("artifact.zip"), "storytold/craft-anything",
                    commit=None, expected_sha256=digest, signer_workflow=None,
                )

    def test_local_attestation_requires_a_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
                "--verify-attestation",
            ])
            self.assertEqual(code, 1)
            self.assertIn("needs OWNER/NAME", receipt["error"])

    @staticmethod
    def shell_run_bodies(path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        bodies = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if line.strip() == "run: |":
                base = len(line) - len(line.lstrip())
                index += 1
                body = []
                while index < len(lines):
                    current = lines[index]
                    if current.strip() and len(current) - len(current.lstrip()) <= base:
                        break
                    body.append(current)
                    index += 1
                bodies.append("\n".join(body))
            else:
                index += 1
        return bodies

    def test_workflow_shell_does_not_interpolate_expressions(self):
        for name in (
            "fork-release-source-check.yml",
            "fork-release-source-preflight.yml",
            "release.yml",
        ):
            path = WORKFLOWS / name
            self.assertTrue(path.is_file(), name)
            for body in self.shell_run_bodies(path):
                self.assertNotIn("${{", body, name)

    def test_release_workflow_waits_for_the_source_preflight(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("source-preflight:", text)
        self.assertIn("needs: source-preflight", text)

    def test_receipt_survives_a_valid_archive_swap_during_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            original = archive.read_bytes()
            replacement = self.second_valid_archive(parent)
            real_verify = verify_release_source.verify_zip

            def replace_then_verify(target, **kwargs):
                os.replace(replacement, archive)
                if hasattr(target, "seek"):
                    target.seek(0)
                return real_verify(target, **kwargs)

            with patch.object(verify_release_source, "verify_zip",
                              side_effect=replace_then_verify):
                receipt = verify_release_source.verify_release_archive(archive)
            self.assertEqual(receipt["archive_sha256"],
                             hashlib.sha256(original).hexdigest())
            self.assertEqual(receipt["file_count"], 2)

    def test_receipt_survives_a_swap_during_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            original = archive.read_bytes()
            replacement = self.second_valid_archive(parent)
            real_extract = verify_release_source.extract_verified_zip

            def replace_then_extract(stream, output_parent, **kwargs):
                os.replace(replacement, archive)
                if hasattr(stream, "seek"):
                    stream.seek(0)
                return real_extract(stream, output_parent, **kwargs)

            with patch.object(verify_release_source, "extract_verified_zip",
                              side_effect=replace_then_extract):
                receipt = verify_release_source.verify_release_archive(archive)
            self.assertEqual(receipt["archive_sha256"],
                             hashlib.sha256(original).hexdigest())

    def test_receipt_survives_a_swap_during_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            original = archive.read_bytes()
            replacement = self.second_valid_archive(parent)

            def stage_with_swap(source, destination):
                digest = hashlib.sha256()
                count = 0
                with destination.open("xb") as staged:
                    first = source.read(1024)
                    count += len(first)
                    digest.update(first)
                    staged.write(first)
                    os.replace(replacement, archive)
                    while chunk := source.read(1024 * 1024):
                        count += len(chunk)
                        digest.update(chunk)
                        staged.write(chunk)
                return count, digest.hexdigest()

            with patch.object(verify_release_source, "stage_archive",
                              side_effect=stage_with_swap):
                receipt = verify_release_source.verify_release_archive(archive)
            self.assertEqual(receipt["archive_sha256"],
                             hashlib.sha256(original).hexdigest())

    def test_corrupt_staged_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)

            def corrupt_after_staging(source, destination):
                count, digest = real_stage(source, destination)
                destination.write_bytes(b"not a zip")
                return count, digest

            real_stage = verify_release_source.stage_archive
            with patch.object(verify_release_source, "stage_archive",
                              side_effect=corrupt_after_staging):
                with self.assertRaisesRegex(IntegrityError,
                                            "changed after hashing"):
                    verify_release_source.verify_release_archive(archive)

    def test_staged_zip_that_cannot_be_read_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)

            def write_corrupt(source, destination):
                data = b"not a zip"
                destination.write_bytes(data)
                return len(data), hashlib.sha256(data).hexdigest()

            with patch.object(verify_release_source, "stage_archive",
                              side_effect=write_corrupt):
                with self.assertRaisesRegex(IntegrityError,
                                            "ZIP cannot be read safely"):
                    verify_release_source.verify_release_archive(archive)

    def test_interrupted_extraction_writes_an_error_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            receipt_path = parent / "receipt.json"
            with patch.object(verify_release_source, "extract_verified_zip",
                              side_effect=IntegrityError("interrupted")):
                code, receipt = self.run_main([
                    "--archive", str(archive), "--report", str(receipt_path),
                    "--root", str(root),
                ])
            self.assertEqual(code, 1)
            self.assertEqual(receipt["status"], "error")
            self.assertIn("interrupted", receipt["error"])
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
                "error",
            )

    def test_receipt_records_the_strict_root_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.sample(parent)
            archive = parent / "release-source.zip"
            package_zip(root, archive)
            code, receipt = self.run_main([
                "--archive", str(archive), "--root", str(root),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(receipt["root_policy"], "strict")

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
