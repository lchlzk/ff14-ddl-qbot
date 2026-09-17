"""Refresh the bundled CN recruitment facts from a pinned upstream commit.

Usage: python tools/update_akhr_data.py [40-character commit SHA]
The data and its license are generated together; runtime never downloads code.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_COMMIT = "593aa9d5b9b87c27eea762994a376f579a6e9038"


def main():
    commit = sys.argv[1] if len(sys.argv) == 2 else DEFAULT_COMMIT
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise SystemExit("Supply an exact upstream commit SHA, not a branch or URL.")
    base = f"https://raw.githubusercontent.com/arkntools/arknights-toolbox-data/{commit}/"

    def read(part):
        with urlopen(Request(base+part, headers={"User-Agent":"NoneBot-QQ-data-builder"}), timeout=30) as response:
            return response.read(4_000_000).decode("utf-8")

    chars = json.loads(read("assets/data/character.json"))
    names = json.loads(read("assets/locales/cn/character.json"))
    tags = json.loads(read("assets/locales/cn/tag.json"))
    license_text = read("LICENSE")
    roster = []
    for key, char in chars.items():
        # 1 = recruitable, 2 = recruitment-only; absent = not recruitable.
        if char.get("recruitment", {}).get("cn") not in (1, 2):
            continue
        tag_ids = [char["profession"], char["position"], *char["tags"]]
        if char["star"] == 6:
            tag_ids.append(11)
        elif char["star"] == 5:
            tag_ids.append(14)
        roster.append({"name":names[key],"star":char["star"],"tags":sorted({tags[str(t)] for t in tag_ids})})
    if len(roster) < 100 or not any(c["name"] == "能天使" for c in roster):
        raise SystemExit("Upstream schema validation failed; existing bundle was not changed.")
    target = Path(__file__).resolve().parents[1] / "bot_tools" / "data"
    target.mkdir(parents=True, exist_ok=True)
    result = {"source":"arkntools/arknights-toolbox-data", "commit":commit,
              "updated":datetime.now(timezone.utc).strftime("%Y-%m-%d"),
              "region":"CN", "operators":roster}
    (target / "akhr.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (target / "arknights-LICENSE.txt").write_text(license_text,encoding="utf-8")
    print(f"Bundled {len(roster)} CN recruitment operators at {commit}.")


if __name__ == "__main__":
    main()
