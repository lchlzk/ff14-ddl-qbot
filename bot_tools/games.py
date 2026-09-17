from __future__ import annotations

import json
import re
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from message_ui import help_panel, panel
from .storage import ToolError


@lru_cache(maxsize=1)
def ak_data() -> dict:
    return json.loads((Path(__file__).with_name("data")/"akhr.json").read_text(encoding="utf-8"))


def recruitment(tags: list[str], short: bool = False) -> list[tuple[tuple[str,...],list[dict]]]:
    data = ak_data()
    valid = {t for op in data["operators"] for t in op["tags"]}
    if not 1 <= len(tags) <= 5 or len(set(tags)) != len(tags):
        raise ToolError("请输入 1～5 个不重复的公招标签。")
    unknown = set(tags)-valid
    if unknown:
        raise ToolError("未知标签："+"、".join(sorted(unknown)))
    if short and set(tags) & {"资深干员","高级资深干员"}:
        raise ToolError("稀有标签请使用 9:00；短时长模式仅计算普通 1～4 星，不模拟稀有标签短时长招募。")
    result = []
    for count in range(1,min(len(tags),3)+1):
        for subset in combinations(tags,count):
            matches = [op for op in data["operators"] if set(subset)<=set(op["tags"]) and (op["star"]<=4 if short else op["star"]>=3) and (op["star"]<6 or (not short and "高级资深干员" in subset))]
            if matches:
                result.append((subset,sorted(matches,key=lambda c:(-c["star"],c["name"]))))
    return sorted(result,key=lambda pair:(-min(c["star"] for c in pair[1]),len(pair[1]),len(pair[0]),pair[0]))


def akhr(raw: str) -> str:
    args = re.split(r"[\s,，]+",raw.strip()) if raw.strip() else []
    data = ak_data()
    if not args or args == ["help"]:
        return help_panel("明日方舟 · 公开招募", ["/akhr 高级资深干员 输出 近战位", "/akhr 治疗 支援 远程位", "/akhr 支援机械 3:50", "默认按 9 小时计算；低星需求用 3:50。", "1～5 个标签，自动组合最多 3 个。"], footer=f"国服入池快照 {data['updated']}；标签脱落不在保证范围。")
    short = args[-1] == "3:50"
    if short or args[-1] == "9:00":
        args.pop()
    result = recruitment(args,short)
    lines = []
    for tags,ops in result[:5]:
        names = "、".join(c["name"] for c in ops[:7])
        if len(ops)>7:
            names += f" 等 {len(ops)} 位"
        lines += [f"{' + '.join(tags)} · 最低 {min(c['star'] for c in ops)}★", names, ""]
    if not lines:
        lines = ["当前时长下没有符合组合的可公招干员。", "支援机械/新手需求请改用 3:50。"]
    return panel("公招组合建议", lines, subtitle=f"国服 · {'3小时50分' if short else '9小时'} · 快照 {data['updated']}", footer="假设所选标签保留；高资需 9 小时。只显示最佳 5 组；没有选择高资时不会混入六星。")
