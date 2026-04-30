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


def update_zip_content(zip_path: Path, filename: str, new_content: str) -> None:
    """
    Updates or adds a single file inside an existing ZIP archive.
    
    To prevent archive corruption, this function:
    1. Creates a temporary ZIP file.
    2. Copies all existing items from the source ZIP to the temp ZIP.
    3. Replaces (or adds) the target 'filename' with 'new_content'.
    4. Atomically replaces the original ZIP with the temp ZIP.

    Args:
        zip_path: Path to the target .zip file.
        filename: The internal name of the file to update (e.g., 'metadata.txt').
        new_content: The string content to write into that file.
    """
    # Create a temp file in the same directory as the target ZIP
    temp_fd, temp_path = tempfile.mkstemp(dir=zip_path.parent)
    os.close(temp_fd)
    
    try:
        replaced = False
        with zipfile.ZipFile(zip_path, "r") as zin:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                # Copy existing contents
                for item in zin.infolist():
                    # Check for filename match (case-insensitive)
                    if item.filename.split("/")[-1].lower() == filename.lower():
                        zout.writestr(item.filename, new_content)
                        replaced = True
                    else:
                        zout.writestr(item, zin.read(item.filename))

                # If the file didn't exist in the ZIP previously, add it now
                if not replaced:
                    zout.writestr(filename, new_content)

        # Brief sleep ensures file handles are fully released on Windows
        time.sleep(0.1)
        os.replace(temp_path, zip_path)
        
    except Exception as e:
        # Cleanup temp file if something went wrong
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e