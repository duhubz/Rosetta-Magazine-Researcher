"""
ZIP Utilities
Provides helper functions for manipulating ZIP archives.
Includes safe-write logic to prevent archive corruption during updates.
"""

import os
import tempfile
import time
import zipfile
from pathlib import Path


def find_member_by_basename(zip_file: zipfile.ZipFile, target_basename: str) -> str | None:
    """
    Return the exact ZipFile member name whose basename matches target
    (case-insensitive), or None. Path components are compared by basename only,
    NOT by full path — so 'Endgame.pdf' won't match 'game.pdf'.

    Args:
        zip_file: An open ZipFile handle to search.
        target_basename: The file's basename to look for (e.g. 'metadata.txt').

    Returns:
        str or None: The full member path inside the archive, if found.
    """
    target = target_basename.lower()
    return next((n for n in zip_file.namelist() if n.split("/")[-1].lower() == target), None)


def update_zip_contents(zip_path: Path, updates: dict[str, str | bytes]) -> None:
    """
    Rewrite multiple members in one atomic pass. Keys are member paths,
    values are the new contents. Members not in the dict are copied through.

    Existing members are matched by case-insensitive basename (consistent
    with find_member_by_basename); keys that match no existing member are
    appended as new members under the given name.

    To prevent archive corruption, this function:
    1. Creates a temporary ZIP file.
    2. Copies all existing items from the source ZIP to the temp ZIP,
       substituting updated contents where a key matches.
    3. Appends any keys that did not exist in the ZIP previously.
    4. Atomically replaces the original ZIP with the temp ZIP.

    Args:
        zip_path: Path to the target .zip file.
        updates: Mapping of {member name: new content}.
    """
    if not updates:
        return

    # Create a temp file in the same directory as the target ZIP
    temp_fd, temp_path = tempfile.mkstemp(dir=zip_path.parent)
    os.close(temp_fd)

    # Case-insensitive basename -> (original key, content) lookup
    pending = {name.split("/")[-1].lower(): name for name in updates}

    try:
        with (
            zipfile.ZipFile(zip_path, "r") as zin,
            zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout,
        ):
            # Copy existing contents, substituting updated members
            for item in zin.infolist():
                basename = item.filename.split("/")[-1].lower()
                key = pending.pop(basename, None)
                if key is not None:
                    zout.writestr(item.filename, updates[key])
                else:
                    zout.writestr(item, zin.read(item.filename))

            # Any updates that didn't exist in the ZIP previously: add them now
            for key in pending.values():
                zout.writestr(key, updates[key])

        # Brief sleep ensures file handles are fully released on Windows
        time.sleep(0.1)
        os.replace(temp_path, zip_path)

    except Exception:
        # Cleanup temp file if something went wrong
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
