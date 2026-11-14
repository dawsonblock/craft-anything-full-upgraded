# V5–V26 source-bundle integrity and verified extraction

The independent V5 source ZIP contains `SOURCE_INTEGRITY.json`. That file
enumerates every regular source file (other than itself), with its relative
POSIX path, byte count and SHA-256 digest. Python cache files are excluded.

The manifest's `root` field records the folder name used when it was written.
It is a label, not part of the content hashes: GitHub's branch and tag archives
name the top-level folder `<repository>-<branch>`, so verification accepts any
single safe top-level folder and reports both names. Every file is still
checked against the manifest's relative paths. Release gates pass
`--require-declared-root` to require the canonical name; the default keeps
GitHub downloads verifiable.

This inventory detects accidental omissions, additions and modifications after
extraction. It does **not** compile the application, prove that the code is
secure, or authenticate a publisher: an adversary who can change both the
manifest and source can create another internally consistent package.

V7 adds a **verified extraction** command that checks the archive before
unpacking it, rechecks the unpacked files before installing the source root,
and rejects names that are ambiguous on macOS or invalid on Windows.

## Verify the ZIP before extracting it

From any folder, with Python 3 and the verification script available:

    python3 scripts/source_bundle_integrity.py verify-zip \
      --archive /path/to/Craft_Anything_v0.4.0_HARDENED_V5_FULL_SOURCE.zip

This reads entries without extracting them and checks the inventory against
every regular member's uncompressed bytes. The script rejects duplicate ZIP
names, traversal paths, symbolic links, extra files, missing files, a manifest
outside a single top-level folder, a nested source root, malformed manifest
data, source size limits and portable filename collisions. The top-level folder
may be renamed (as GitHub branch archives do); the result reports
`declared_root`, the actual `source_root`, `root_policy` (`strict` or
`compatible`) and `root_renamed`. Add
`--require-declared-root` to refuse a renamed folder. The V7 rules also reject
Windows device names, control characters, non-normalized Unicode, unsafe
filename punctuation and trailing dots/spaces.

## Package a source ZIP and verify the artifact (V26)

Build the archive from the verified tree, then verify the file that was actually
written rather than a checked-out directory:

    python3 scripts/source_bundle_integrity.py package-zip \
      --root . --archive /path/to/new-source.zip

The command refuses a stale tree, writes every manifest entry plus the manifest
itself under the manifest's `root`, refuses an archive inside the source tree,
and always requires the canonical root. It creates the destination exclusively
(`O_CREAT | O_EXCL`), so a file another process creates during packaging is
never overwritten; it then verifies the bytes written to that descriptor, not a
separate path. Before creating the destination it also verifies the staged ZIP
and extracts it into a private folder, rehashing every file. Every member is
written with a fixed 1980 timestamp and mode, so identical source produces an
identical ZIP regardless of checkout times. Its result includes the whole
archive's SHA-256. The source-portability workflow runs this package, verify
and extract round-trip with `--require-declared-root` on every operating system
it tests.

## Verify a published release artifact

Check the file people actually download, not a fresh ZIP built in CI:

    python3 scripts/verify_release_source.py \
      --repo OWNER/NAME --tag vX.Y.Z --asset '*source*.zip' \
      --report /path/outside/the/checkout/receipt.json

The script downloads exactly one matching asset with the GitHub CLI, requires
the canonical root, checks every member without extracting it, unpacks it into
a private temporary folder and rehashes the tree. An already downloaded file
can be checked with `--archive`. The archive is copied through a bounded stream
into a private staging file and hashed while it is copied; that staged copy is
re-hashed, verified and extracted, and is re-hashed again afterwards. The
receipt's SHA-256 therefore describes exactly the bytes that were verified,
even if the original path is replaced or changed meanwhile. Repository, tag and
asset inputs are validated before `gh` runs. The receipt records the release
identity, the archive's SHA-256, the manifest's SHA-256, the file count, total
bytes and the root policy (`strict` here); it is created exclusively, never
overwritten, and must stay outside the checkout. A release that attaches no
source ZIP, or attaches a GitHub-generated archive without
`SOURCE_INTEGRITY.json`, fails with a plain error. The **Release Source
Artifact Check** workflow passes those values through environment variables
rather than inserting expressions into its shell, runs this when a release is
published and on manual dispatch, and uploads the receipt.

## Bind a published asset to a preflight receipt

`fork-release-source-preflight.yml` runs before the platform release jobs (and
on manual dispatch). It verifies the source tree, packages the canonical ZIP,
verifies and extracts it, writes a receipt bound to the release commit, and
attests the ZIP with GitHub's artifact attestation. `release.yml` gates the
platform jobs on it, then downloads the source ZIP attached to the resulting
draft release and checks it against that receipt. Draft releases do not create
their Git tag until publication, so the draft check addresses the release by
ID and verifies a local archive.

Check a draft asset (the `source-verify` job does this automatically):

    python3 scripts/verify_release_source.py \
      --archive /path/to/draft-source.zip \
      --provenance /path/to/release-source-preflight.json \
      --verify-attestation OWNER/NAME \
      --report /path/outside/the/checkout/receipt.json

After publication, check the tag as well:

    python3 scripts/verify_release_source.py \
      --repo OWNER/NAME --tag vX.Y.Z \
      --provenance /path/to/release-source-preflight.json \
      --verify-attestation \
      --report /path/outside/the/checkout/receipt.json

`--provenance` requires the asset's SHA-256 to equal the preflight receipt's.
In remote mode, `--commit` (or the commit recorded in the receipt) must equal
the commit the release tag resolves to. `--verify-attestation` asks
`gh attestation verify` to check the ZIP's signed GitHub artifact attestation,
enforces that source commit, and requires the attestation's subject digest to
be the same SHA-256. The default signer is
`OWNER/NAME/.github/workflows/fork-release-source-preflight.yml`; pass
`--signer-workflow` when a release uses another workflow. A local archive can
also be checked with `--archive --expect-archive-sha256`.

The attestation proves which repository and workflow produced the ZIP and which
commit it came from. It does not prove that the code was reviewed, that the
build is reproducible, or that someone with release access could not replace
both the manifest and the source; those remain release-process checks.

## Verified extraction (V7)

Use a **separately trusted copy** of this script. Never execute a verifier
obtained from an untrusted ZIP merely to decide whether that ZIP is safe.
From the trusted source folder, with an existing private output parent:

```bash
python3 scripts/source_bundle_integrity.py extract-zip \
  --archive /path/to/Craft_Anything_v0.4.0_HARDENED_V7_FULL_SOURCE.zip \
  --output-parent /path/to/empty-install-parent
```

The command creates the archive's single top-level folder under
`empty-install-parent` only after both pre-extraction ZIP validation and
post-extraction source hashing pass. It keeps that folder's name, so a GitHub
branch archive installs as `<repository>-<branch>` and a release ZIP installs as
`craft-anything-full-upgraded`. Pass `--require-declared-root` to refuse a
renamed archive before anything is extracted. It does not run any file from the
archive. Existing destination files or folders are not intentionally
overwritten. Errors clean the private staging folder; keep the output parent
**private**, as concurrent writers are outside the protection of this
cross-platform rename procedure. Run it again only after choosing another empty
output parent or moving your existing tree.

To test the extraction rules separately on three operating systems, trigger
the manually dispatched **Independent Fork Source Portability** workflow in
your own GitHub repository. The workflow is provided but was not executed
in the packaging environment.

For greater assurance, compare the **whole ZIP's SHA-256** to the checksum
published alongside your copy of V5. The internal file manifest alone is not
a signature.

## Verify the extracted source tree

From the extracted source directory (its name may differ from the manifest's
`root`, for example after a GitHub branch download):

    python3 scripts/source_bundle_integrity.py verify-tree --root .

Add `--require-declared-root` to refuse a directory whose name differs from the
manifest's `root`; the result reports `root_policy` (`strict` or `compatible`).

The command recomputes every source digest and rejects unexpected files.
It deliberately ignores checkout metadata under `.git`, the root-level folders
that are local scratch rather than source (`target`, `log`, `plan`, `plans`,
`screenshots`, `corpus`, `dist`), local secret material Git refuses to commit
(`.env`, `.env.*`, `*.pem`, `*.p12`, `*.pfx`, `*.key`), backup files (`*~`),
tool settings under `.claude`, and the caches local tools leave beside the code
(`__pycache__`, `.pyc`, `.ruff_cache`, `.pytest_cache`, `.mypy_cache`, `.tox`,
`.nox`, `.DS_Store`). Generated output anywhere else inside the source tree is
treated as an unexpected source change. A custom `CARGO_TARGET_DIR` should
point outside the source tree or inside its root `target/` directory.

If you deliberately edit the source, this verification should **fail** until
you generate a new manifest for your own modified fork:

    python3 scripts/source_bundle_integrity.py create --root .

Inside a Git work tree root, `create` lists files with `git ls-files` and
records exactly what a clone can contain. It refuses to write a manifest when
the tree holds files Git does not track, naming them, and `--no-tracked` falls
back to walking the tree. A manifest that recorded a Git-ignored file — a tool
cache, a build output — could never be rebuilt from a clone, and every clone
would fail verification. Verification itself never needs Git.

Never interpret a regenerated self-consistent manifest as independent
verification of who authored or reviewed the modifications.

## Scope and resource bounds

- Python standard library only, and no execution of archived source code.
  `create` may call `git ls-files`; verification never runs a program.
- Up to 20,000 source files, 100 MB per source file, 2 GB total unpacked
  source data and a 5 MB manifest.
- ZIP names must use portable, unambiguous relative paths without traversal,
  invalid Windows names, file/parent conflicts or case-insensitive collisions,
  and exactly one safe top-level folder.
- The original license files, assets, Rust crates, tests and docs are all
  included in the inventory.

This is a packaging and provenance-control aid. Native Rust compilation,
third-party dependency verification, executable sandboxing and genuine
gameplay tests are independent release gates.
