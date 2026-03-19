"""ZIP file manipulation utilities."""

import os
import tempfile
import time
import zipfile
from pathlib import Path


def update_zip_content(zip_path: Path, filename: str, new_content: str) -> None:
    """Update or add a file inside a ZIP archive."""
    temp_fd, temp_path = tempfile.mkstemp(dir=zip_path.parent)
    os.close(temp_fd)
    try:
        replaced = False
        with zipfile.ZipFile(zip_path, "r") as zin:
            with zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as zout:
                for item in zin.infolist():
                    if item.filename.split("/")[-1].lower() == filename.lower():
                        zout.writestr(item.filename, new_content)
                        replaced = True
                    else:
                        zout.writestr(item, zin.read(item.filename))

                if not replaced:
                    zout.writestr(filename, new_content)

        time.sleep(0.1)
        os.replace(temp_path, zip_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
