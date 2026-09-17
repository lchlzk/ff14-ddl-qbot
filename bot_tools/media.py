from __future__ import annotations

import io
import math
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from message_ui import help_panel, panel
from .storage import Identity, Store, ToolError, clean
from . import gallery
from . import http_clients

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_GIF_FRAMES = 1000
MAX_GIF_TOTAL_PIXELS = 200_000_000
Image.MAX_IMAGE_PIXELS = 12_000_000
RENDER_LOCK = threading.Lock()


def checked_image(data: bytes) -> bytes:
    if len(data) > MAX_IMAGE_BYTES:
        raise ToolError("图片不能超过 4MB。")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width*source.height > 12_000_000 or source.format not in {"PNG","JPEG","WEBP","GIF"}:
                raise ToolError("仅接受普通 PNG/JPEG/WebP/GIF 图片，最多 1200 万像素。")
            if source.format == "GIF":
                # Validate each frame, but retain the exact GIF bytes. Re-encoding
                # can change palette, transparency, disposal and frame timing.
                # Reject excessive decode work instead of silently truncating it.
                pixels = 0
                for frame in range(MAX_GIF_FRAMES + 1):
                    try:
                        source.seek(frame)
                    except EOFError:
                        break
                    if source.width * source.height > 12_000_000:
                        raise ToolError("GIF 单帧不能超过 1200 万像素；不会截取第一帧入库。")
                    pixels += source.width * source.height
                    if frame >= MAX_GIF_FRAMES or pixels > MAX_GIF_TOTAL_PIXELS:
                        raise ToolError("GIF 帧数或累计像素过多，请缩小尺寸或缩短动画后重试；不会截取第一帧入库。")
                    source.load()
                return data
            source.seek(0)
            result = source.convert("RGB")
            result.thumbnail((1200,1200))
            output = io.BytesIO()
            result.save(output,"JPEG",quality=88,optimize=True)
            return output.getvalue()
    except ToolError:
        raise
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise ToolError("图片损坏、过大或格式不受支持。") from None


def valid_image_url(url: str, source: str = "qq") -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None,443}:
            return False
        if source == "cat":
            return parsed.hostname in {"cdn2.thecatapi.com","cdn.thecatapi.com"} or (parsed.hostname == "s3.us-west-2.amazonaws.com" and parsed.path.startswith("/cdn2.thecatapi.com/images/"))
        return parsed.hostname in {"gchat.qpic.cn","c2cpicdw.qpic.cn","multimedia.nt.qq.com.cn","qqbot.ugcimg.cn"}
    except ValueError:
        return False


async def fetch_image(url: str, source: str = "qq") -> bytes:
    # Fixed first-party CDN hosts only. No redirects, local addresses, user URLs,
    # file://, proxy environment, or anonymous third-party anime image providers.
    if url.startswith("//"):
        url = "https:"+url
    if not valid_image_url(url,source):
        raise ToolError("该图片地址不在允许的图片 CDN 中。请直接上传 QQ 图片，不要发送网页链接。")
    try:
        async with http_clients.client("qq-media", timeout=15,follow_redirects=False,trust_env=False) as client:
            async with client.stream("GET",url) as response:
                response.raise_for_status()
                if not response.headers.get("content-type","").startswith("image/"):
                    raise ToolError("服务器返回的不是图片。")
                buf = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    buf.extend(chunk)
                    if len(buf)>MAX_IMAGE_BYTES:
                        raise ToolError("图片超过 4MB，已停止下载。")
                return bytes(buf)
    except httpx.HTTPError:
        raise ToolError("图片下载失败或已过期，请稍后重发图片。") from None


async def cat_picture() -> bytes:
    try:
        async with http_clients.client("cat-api", timeout=12,follow_redirects=False,trust_env=False) as client:
            response = await client.get("https://api.thecatapi.com/v1/images/search",params={"mime_types":"jpg,png","size":"small","limit":1})
            response.raise_for_status()
            if len(response.content)>32768:
                raise ToolError("猫图接口返回异常。")
            url = response.json()[0]["url"]
        return await fetch_image(url,"cat")
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):
        raise ToolError("猫图服务暂时不可用；你也可发送 /image add cat 并附图，添加本地猫图。") from None


def gallery_category(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff_-]{1,20}",text):
        raise ToolError("分类限 1～20 个汉字、字母、数字、横线或下划线。")
    return text.lower()


def gallery_add(store: Store, who: Identity, category: str, data: bytes,
                expected: gallery.GalleryTarget | None = None) -> int:
    category = gallery_category(category)
    image = checked_image(data)
    return gallery.add(store, who, category, image, expected)


def gallery_get(store: Store, who: Identity, category: str,
                expected: gallery.GalleryTarget | None = None) -> bytes | None:
    return gallery.get(store, who, gallery_category(category), expected)


def image_filename(data: bytes) -> str:
    return "gallery.gif" if data.startswith((b"GIF87a", b"GIF89a")) else "gallery.jpg"


def gallery_command(store: Store, who: Identity, raw: str) -> str:
    args = raw.split()
    if not args or args == ["help"]:
        target = gallery.current(store, who)
        return help_panel("分类图库 · " + target.label, ["/image 分类（随机一张）", "/image list [分类] [page=页码]",
            "所有人：/image add 分类，并在同一条消息上传 1～10 张图片",
            ("总管理员" if target.public else "管理员") + "：/image del 编号",
            "/image status · 查看当前图库和容量",
            "/cat、/waifu 使用当前图库的对应分类",
            ("私聊固定使用公共图库" if who.private else
             "群主切换：/group gallery public 或 local")],
            footer="公共库共享且不设张数上限；本会话库限 100 张。单次最多 10 张、每张最多 4MB，GIF 完整保存。只上传有权使用、适合公开展示的图片。")
    if args == ["status"]:
        target, count, size = gallery.stats(store, who)
        return panel("图库状态", ["当前：" + target.label,
            f"图片：{count} 张" + ("（不设张数上限）" if target.public else " / 100 张"),
            f"图片数据：{size / 1024 / 1024:.2f} MiB",
            "公共图片跨群共享，仅总管理员可删除。" if target.public else "仅当前会话可见，管理员可删除。"],
            footer="未设置张数上限时仍受可用容量限制；切换图库不会搬迁旧图。")
    if args[0] == "list":
        rest, page = args[1:], 1
        if rest and rest[-1].startswith("page="):
            value = rest.pop()[5:]
            if not value.isascii() or not value.isdigit() or len(value) > 9 or int(value) < 1:
                raise ToolError("页码应为正整数，例如 /image list cat page=2。")
            page = int(value)
        if len(rest) > 1:
            raise ToolError("用法：/image list [分类] [page=页码]")
        category = gallery_category(rest[0]) if rest else None
        target, rows, total = gallery.listing(store, who, category, page)
        pages = max(1, (total + gallery.PAGE_SIZE - 1) // gallery.PAGE_SIZE)
        if page > pages:
            raise ToolError(f"页码超出范围，当前共 {pages} 页。")
        lines = ([f"{r[0]} · {r[1]} 张" for r in rows] if category is None else ["、".join(str(r[0]) for r in rows)])
        if not rows:
            lines = ["图库为空，所有人都可发送 /image add 分类 并附带图片。"]
        return panel("图库分类" if category is None else "图库编号 · " + category, lines,
            subtitle=f"{target.label} · 第 {page}/{pages} 页",
            footer=("总管理员" if target.public else "管理员") + "移除：/image del 编号；翻页：/image list " + (category + " " if category else "") + "page=2")
    if args[0] == "del" and len(args) == 2 and args[1].isascii() and args[1].isdigit() and len(args[1]) <= 18:
        gallery.delete(store, who, int(args[1]))
        return panel("图片已移除", "已从当前图库删除；如需恢复请重新上传原图。")
    raise ToolError("用法：/image 分类 | list [分类] [page=页码] | add 分类+1～10 张图片 | del 编号 | status")


def font(size: int):
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msyh.ttc"):
        if Path(path).exists():
            return ImageFont.truetype(path,size)
    return ImageFont.load_default(size=size)


def render_gif(raw: str) -> bytes:
    text = clean(raw,36)
    rows = [text[i:i+12] for i in range(0,len(text),12)]
    frames = []
    text_font, small = font(40),font(18)
    for frame in range(18):
        canvas = Image.new("RGB",(640,320),(237,243,252))
        draw = ImageDraw.Draw(canvas)
        shift = round(math.sin(frame/18*math.tau)*6)
        draw.rounded_rectangle((28,28+shift,612,286+shift),24,fill=(255,255,255))
        draw.rounded_rectangle((56,54+shift,106,60+shift),3,fill=(91,128,218))
        draw.text((122,44+shift),"QQ BOT · 动态文字",fill=(92,112,142),font=small)
        for i,row in enumerate(rows):
            width = draw.textlength(row,font=text_font)
            draw.text(((640-width)/2,111+i*48-(len(rows)-1)*12+shift),row,font=text_font,fill=(40,58,87))
        for i in range(3):
            radius = 4+(frame//6==i)*2
            draw.ellipse((304+i*16-radius,258-radius,304+i*16+radius,258+radius),fill=(117,153,230))
        frames.append(canvas)
    stream = io.BytesIO()
    frames[0].save(stream,"GIF",save_all=True,append_images=frames[1:],duration=90,loop=0,optimize=True)
    return stream.getvalue()


def render_tex(raw: str) -> bytes:
    expression = raw.strip()
    if expression.startswith("$") and expression.endswith("$"):
        expression = expression[1:-1]
    if not expression or len(expression)>240 or "$" in expression or any(ord(c)<32 for c in expression):
        raise ToolError("请输入一行、最多 240 字符的公式，例如 /tex \\frac{a}{b}。")
    if re.search(r"\\(?:input|include|write|openout|read|usepackage|def|newcommand|special|href|url)\b",expression):
        raise ToolError("仅支持数学公式子集，不允许文件、宏和外部命令。")
    # Mathtext has no TeX subprocess or shell escape. The lock serializes parser/font caches.
    with RENDER_LOCK:
        from matplotlib import mathtext, rc_context
        from matplotlib.font_manager import FontProperties
        try:
            with rc_context({"text.usetex":False,"mathtext.fontset":"stix"}):
                prop = FontProperties(size=24)
                parsed = mathtext.MathTextParser("agg").parse("$"+expression+"$",dpi=150,prop=prop)
                if parsed.width>2200 or parsed.height>1000:
                    raise ToolError("公式图片过大，请拆成较短的公式。")
                stream = io.BytesIO()
                mathtext.math_to_image("$"+expression+"$",stream,prop=prop,dpi=150,format="png",color="#263b60")
                with Image.open(stream) as rendered:
                    canvas = Image.new("RGB",(rendered.width+72,rendered.height+104),"#f0f4fb")
                    draw = ImageDraw.Draw(canvas)
                    draw.rounded_rectangle((12,12,canvas.width-12,canvas.height-12),16,fill="white")
                    canvas.paste(rendered.convert("RGBA"),(36,52),rendered.convert("RGBA"))
                    draw.text((30,20),"MATH · 公式",font=font(16),fill="#7987a1")
                    output = io.BytesIO()
                    canvas.save(output,"PNG")
                    return output.getvalue()
        except (ValueError,RuntimeError,RecursionError):
            raise ToolError("公式无法解析。支持分数、根号、积分、上下标等 Mathtext 语法，不支持完整 LaTeX 文档。") from None


# An intentionally small, local phrase-pair aid, not an AI language model.
PHRASE_PAIRS = [("春风","秋月"),("明月","清风"),("青山","绿水"),("千山","万水"),("千帆","万里"),("红花","翠柳"),("白云","碧水"),("春雨","秋风"),("山川","日月"),("天地","乾坤"),("书香","墨韵"),("人间","天上"),("故人","新客"),("迎春","辞岁"),("入梦","归心"),("花开","叶落"),("鸟语","花香"),("江南","塞北"),("朝霞","夕照"),("长天","远水"),("满院","一庭"),("映","照"),("山","水"),("天","地"),("风","月"),("花","柳"),("云","雨"),("春","秋"),("来","去"),("高","远"),("长","短"),("红","绿"),("新","旧"),("日","夜"),("东","西"),("南","北"),("千","万"),("一","百")]


def duilian(raw: str) -> str:
    if not raw or raw == "help":
        return help_panel("对联 · 离线对仗助手", ["/duilian 春风映青山", "当前按本地词组生成对仗草稿。", "原在线对联服务未通过连通性检查，未接入。"], footer="不是 AI 自由创作，不保证平仄、语义或文学质量；不向外发送文字。")
    text = clean(raw,40)
    if not re.fullmatch(r"[\u4e00-\u9fff，。！？、；]+",text):
        raise ToolError("请输入中文上联和中文标点，最多 40 字。")
    mapping = {}
    for a,b in PHRASE_PAIRS:
        mapping.setdefault(a,b)
        mapping.setdefault(b,a)
    result, matched, index = [],0,0
    while index<len(text):
        token = next((text[index:index+n] for n in (2,1) if text[index:index+n] in mapping),None)
        if token:
            result.append(mapping[token]); matched+=len(token); index+=len(token)
        else:
            result.append(text[index]); index+=1
    if not matched:
        raise ToolError("离线词库暂不能为这句提供对仗。可试：/duilian 春风映青山；自由创作需另接可用模型。")
    return panel("对仗草稿 · 离线", ["上联："+text,"草稿："+"".join(result)], footer="只替换已知对仗词，未覆盖部分保留原文；请自行调整语义和平仄。")
