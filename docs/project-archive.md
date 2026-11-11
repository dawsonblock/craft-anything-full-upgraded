# Recoverable project removal

The desktop Archive action is the default way to remove a project from the
active list. It calls the human-only project_archive tool with explicit
confirmation; an active build must be stopped first. The separate
project_delete tool still permanently deletes Craft Anything-owned data
when a person explicitly invokes it with confirmation.

## Where the archive goes

An active folder such as:

    Projects/snake/

is moved into a collision-safe sibling archive:

    Projects/CraftAnything-Trash-snake-1/
      receipt.json
      project/
        craft.json
        app/
        checks/
        ...

The archive receives an incremented number if a similarly named folder
already exists. Nothing already at that name is overwritten. A receipt
records the version, original folder, project name, timestamp, whether the
app was edited in place, and whether archiving finished.

User-owned files remain where they originally were. In particular, if
the project edited an existing app in place, those original app files are
not moved into the archive.

## Restoring a completed archive

V3 provides the **human-only** `project_archive_restore` engine tool and CLI
command. It is not yet exposed as a dedicated desktop Restore button. The
tool takes an archived folder, the explicit original path from the receipt,
and `confirm: true`. For example:

    craft-anything-cli project-archive-restore \
      --json '{"archive":"/path/to/Projects/CraftAnything-Trash-snake-1","original_folder":"/path/to/Projects/snake","confirm":true}'

The receipt must indicate `status: archived`, use version 1, and match the
saved project manifest. The original location must be an absolute path and
must be confirmed explicitly by the person. The archive must be a real
directory beside the original project; symlinks are refused.

Before moving anything, the tool checks **every** owned entry for collisions.
It refuses to overwrite or merge with an existing entry—even a dangling
symlink—while retaining user-owned files already in the original folder.
It restores the manifest last and marks the archive receipt `restored`.
Any interrupted move leaves a `restoring` receipt and requires manual
reconciliation. A successful restore leaves the receipt in the archive folder.

## Recovering a project manually

Use the manual procedure below only when you have independently checked the
contents and the automated restore refused (for example, a collided folder
or an incomplete archive). There is **no one-click Restore button yet**.

1. Stop any build using the project. Open the archive path shown in the
   success message and verify receipt.json has status "archived".
2. Read original_folder from the receipt. Confirm manually that the
   destination is the project folder you expect.
3. If the original folder remains with your own files, preserve those files
   and make a backup before proceeding. If it no longer exists, create it.
4. Move the contents of archive/project/ **into the original folder**.
   Stop if any destination file or folder already exists; never choose a
   blind "replace all" operation. The archived files contain the original
   craft.json manifest and generated records, so merging a live project
   over them would destroy changes.
5. Open the restored project normally. For an in-place edited project,
   make sure its original app files still exist at the original location.
6. Keep the receipt until you have confirmed the restored project opens
   and its files are complete.

Do not automatically restore archives with status "archiving" or "restoring":
a power failure or filesystem error may have moved only some files. Inspect
both the original and archive folders and reconcile each entry manually.

## Limits

- This is a same-volume, per-file rename sequence, not an atomic database
  transaction. Partial failures can leave entries in both locations.
- Recoverable means the generated files are kept, **not** that there is
  an automatic desktop Undo button or that the original user's in-place app
  has been duplicated. A confirmed CLI/tool restore is available in V3.
- No program or agent is permitted to treat the receipt as authorization
  to overwrite arbitrary files in the original folder.
