"""Run tests without loading the operator's .env or touching live bot data."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    with tempfile.TemporaryDirectory(prefix="qqbot-tests-") as temp:
        os.environ["BOT_DATA_DIR"] = temp
        os.environ["MPLCONFIGDIR"] = str(Path(temp) / "matplotlib")
        os.environ["LOG_LEVEL"] = "ERROR"
        os.environ["OTTER_GLOBAL_MIN_INTERVAL"] = "0"
        import nonebot
        # A tuple bypasses the automatic addition of the real root .env.
        nonebot.init(_env_file=(Path(temp) / "test.env",), driver="~fastapi+~httpx+~websockets", qq_bots=[])
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=sys.argv[1] if len(sys.argv)>1 else "test_*.py")
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
