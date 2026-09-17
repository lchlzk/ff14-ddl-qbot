"""Create the distributable ZIP from an explicit offline-runtime allowlist."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import zipfile


FILES = (
    ".env.example", "bot.cmd", "bot.ps1", "bot.sh", "menu.cmd", "menu.ps1", "menu.sh",
    "qq-menu.json", "compose.yaml", "START.txt", "TOOLBOX.md", "AI.md",
    "LEARNING_CHAT.md", "WEB_ADMIN.md", "TRICKCAL.md", "AGPL-3.0-only.txt", "OPTIMIZATIONS.md",
    "nonebot-qq.tar", "image-id.txt",
)


def pack(root: Path) -> Path:
    root = root.resolve(strict=True)
    if (root / ".env").exists():
        raise ValueError("A real .env must never enter the release folder")
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or path.resolve().parent != root:
            raise ValueError(f"Missing or unsafe release file: {name}")
    destination = root / "QQbot-one-click.zip"
    temporary = root / ".QQbot-one-click.zip.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for name in FILES:
                archive.write(root / name, arcname=name)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad or set(archive.namelist()) != set(FILES):
                raise ValueError("ZIP validation failed")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    with destination.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    print(f"Offline installer ZIP: {destination.stat().st_size} bytes; SHA256 {digest}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    pack(parser.parse_args().release)
