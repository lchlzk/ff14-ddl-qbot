"""Compact Chinese role-name normalization used by the Trickcal plugin."""
from __future__ import annotations


# The public sources mix zh-CN and zh-TW names.  This domain-specific map
# covers the current character catalogue and common name characters without
# adding a multi-megabyte general-purpose conversion dependency.
TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "達": "达", "師": "师", "號": "号", "寧": "宁", "頭": "头",
    "傑": "杰", "愛": "爱", "麗": "丽", "貝": "贝", "爾": "尔",
    "盧": "卢", "謝": "谢", "亞": "亚", "萊": "莱", "瑪": "玛",
    "馬": "马", "約": "约", "魯": "鲁", "喬": "乔", "劉": "刘",
    "蓮": "莲", "錫": "锡", "庫": "库", "綾": "绫", "茲": "兹",
    "蘭": "兰", "羅": "罗", "絲": "丝", "內": "内", "瓏": "珑",
    "諾": "诺", "優": "优", "圖": "图", "凱": "凯", "蘇": "苏",
    "緹": "缇", "蘿": "萝", "維": "维", "歐": "欧", "賽": "赛",
    "嵐": "岚", "瀧": "泷", "櫻": "樱", "夢": "梦", "龍": "龙",
    "華": "华", "雲": "云", "葉": "叶", "樂": "乐", "潔": "洁",
    "靈": "灵", "薩": "萨", "奧": "奥", "溫": "温", "緣": "缘",
    "㐅": "乂",
})


def simplify_role_name(value: str) -> str:
    return value.translate(TRADITIONAL_TO_SIMPLIFIED)
