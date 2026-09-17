"""Build-only pruning of known dependency self-tests, never runtime resources.

Keep numpy.testing, matplotlib's mpl-data, fonts, metadata, and licenses.
Run in the same Docker layer as pip so removed files do not enter the export.
"""
from pathlib import Path
import shutil
import sys
import sysconfig


def prune(root: Path) -> int:
    root = root.resolve(strict=True)
    removed = 0
    for package in ("numpy", "matplotlib", "mpl_toolkits"):
        base = root / package
        if not base.is_dir() or base.is_symlink():
            continue
        for target in sorted(base.rglob("tests"), key=lambda p: len(p.parts), reverse=True):
            if not target.is_dir() or target.is_symlink():
                continue
            resolved = target.resolve(strict=True)
            if not resolved.is_relative_to(root / package):
                raise RuntimeError("Refusing to prune a path outside its dependency")
            removed += sum(p.stat().st_size for p in target.rglob("*") if p.is_file() and not p.is_symlink())
            shutil.rmtree(resolved)
    return removed


if __name__ == "__main__":
    if sys.platform != "linux" or sys.prefix != "/usr/local":
        raise SystemExit("This helper is only for the Python Docker build environment")
    print(f"Removed {prune(Path(sysconfig.get_path('purelib')))} bytes of dependency self-tests")
