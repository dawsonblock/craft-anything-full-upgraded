# V5–V7 source-bundle integrity and verified extraction

The independent V5 source ZIP contains `SOURCE_INTEGRITY.json`. That file
enumerates every regular source file (other than itself), with its relative
POSIX path, byte count and SHA-256 digest. Python cache files are excluded.

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
names, traversal paths, symbolic links, extra files, missing files, a mismatched
root, malformed manifest data, source size limits and portable filename
collisions. The V7 rules also reject Windows device names, control characters,
non-normalized Unicode, unsafe filename punctuation and trailing dots/spaces.

## Verified extraction (V7)

Use a **separately trusted copy** of this script. Never execute a verifier
obtained from an untrusted ZIP merely to decide whether that ZIP is safe.
From the trusted source folder, with an existing private output parent:

```bash
python3 scripts/source_bundle_integrity.py extract-zip \
  --archive /path/to/Craft_Anything_v0.4.0_HARDENED_V7_FULL_SOURCE.zip \
  --output-parent /path/to/empty-install-parent
```

The command creates `empty-install-parent/craft-anything-full-upgraded` only
after both pre-extraction ZIP validation and post-extraction source hashing
pass. It does not run any file from the archive. Existing destination files
or folders are not intentionally overwritten. Errors clean the private staging
folder; keep the output parent **private**, as concurrent writers are outside
the protection of this cross-platform rename procedure. Run it again only
after choosing another empty output parent or moving your existing tree.

To test the extraction rules separately on three operating systems, trigger
the manually dispatched **Independent Fork Source Portability** workflow in
your own GitHub repository. The workflow is provided but was not executed
in the packaging environment.

For greater assurance, compare the **whole ZIP's SHA-256** to the checksum
published alongside your copy of V5. The internal file manifest alone is not
a signature.

## Verify the extracted source tree

From the extracted `craft-anything-full-upgraded` directory:

    python3 scripts/source_bundle_integrity.py verify-tree --root .

The command recomputes every source digest and rejects unexpected files.
It deliberately ignores Python's `__pycache__` and `.pyc` test artifacts,
checkout metadata under `.git`, and (as of V8) Cargo's **root-level**
`target/` build directory. Generated output elsewhere inside the source tree
is treated as an unexpected source change. A custom `CARGO_TARGET_DIR` should
point outside the source tree or inside its root `target/` directory.

If you deliberately edit the source, this verification should **fail** until
you generate a new manifest for your own modified fork:

    python3 scripts/source_bundle_integrity.py create --root .

Never interpret a regenerated self-consistent manifest as independent
verification of who authored or reviewed the modifications.

## Scope and resource bounds

- Python standard library only; no execution of archived source code.
- Up to 20,000 source files, 100 MB per source file, 2 GB total unpacked
  source data and a 5 MB manifest.
- ZIP names must use portable, unambiguous relative paths without traversal,
  invalid Windows names, file/parent conflicts or case-insensitive collisions.
- The original license files, assets, Rust crates, tests and docs are all
  included in the inventory.

This is a packaging and provenance-control aid. Native Rust compilation,
third-party dependency verification, executable sandboxing and genuine
gameplay tests are independent release gates.
