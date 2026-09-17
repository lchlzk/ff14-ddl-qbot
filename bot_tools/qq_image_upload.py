"""Upload generated images to QQ's temporary COS and return safe Markdown URLs."""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from nonebot.adapters.qq import Bot
from nonebot.drivers import Request


MAX_IMAGE_BYTES = 8 * 1024 * 1024
URL_SAFETY_MARGIN = 120
_url_cache: dict[tuple[str, str, str, str], tuple[float, str]] = {}


@dataclass(frozen=True)
class UploadTarget:
    kind: str
    target_id: str


def _safe_raw_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not hostname.endswith(".myqcloud.com")
        or ".cos." not in hostname
        or not parsed.path
        or parsed.fragment
    ):
        return ""
    return value


def _markdown_image_url(raw_url: str) -> str:
    """Make QQ's COS object render as an image in Markdown clients.

    QQ stores multipart uploads with ``application/octet-stream`` even when the
    filename and bytes are JPEG.  COS permits this response-header override on
    the returned presigned URL, and QQ's Markdown renderer then decodes it as an
    image instead of showing an empty placeholder.
    """
    parsed = urlsplit(raw_url)
    if any(
        name == "response-content-type"
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return raw_url
    separator = "&" if parsed.query else "?"
    # Preserve every signed COS query byte exactly; only append an unsigned
    # response override, which QQ's returned signature explicitly permits.
    return f"{raw_url}{separator}response-content-type=image%2Fjpeg"


def _cached(key: tuple[str, str, str, str]) -> str:
    entry = _url_cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    if entry:
        _url_cache.pop(key, None)
    return ""


async def _finish_upload(bot: Bot, target: UploadTarget, upload_id: str) -> dict:
    base = bot.adapter.get_api_base()
    if target.kind == "group":
        url = base.joinpath("v2", "groups", target.target_id, "files")
    elif target.kind == "c2c":
        url = base.joinpath("v2", "users", target.target_id, "files")
    else:
        raise ValueError("QQ upload target must be group or c2c")
    result = await bot._request(Request("POST", url, json={"upload_id": upload_id}))
    if not isinstance(result, dict):
        raise RuntimeError("QQ image upload returned an invalid completion response")
    return result


async def public_image_url(
    bot: Bot,
    target: UploadTarget,
    image: bytes,
    *,
    filename: str = "trickcal-card.jpg",
) -> str:
    """Upload one JPEG through QQ's multipart API and cache its temporary GET URL."""
    if not image.startswith(b"\xff\xd8\xff"):
        raise ValueError("QQ Markdown card must be a JPEG image")
    if not 0 < len(image) <= MAX_IMAGE_BYTES:
        raise ValueError("QQ Markdown card is too large")
    if target.kind not in {"group", "c2c"} or not target.target_id:
        raise ValueError("Unsupported QQ upload target")

    digest = hashlib.sha256(image).hexdigest()
    key = (str(bot.self_id), target.kind, target.target_id, digest)
    if cached := _cached(key):
        return cached

    hashes = {
        "md5": hashlib.md5(image).hexdigest(),
        "sha1": hashlib.sha1(image).hexdigest(),
        "md5_10m": hashlib.md5(image[: 10 * 1024 * 1024]).hexdigest(),
    }
    stem = "".join(character for character in filename.rsplit(".", 1)[0] if character.isalnum() or character in "-_")
    safe_name = f"{(stem or 'trickcal-card')[:40]}-{digest[:16]}.jpg"
    prepare_kwargs = {
        "file_type": 1,
        "file_name": safe_name,
        "file_size": len(image),
        **hashes,
    }
    if target.kind == "group":
        prepare = await bot.post_group_upload_prepare(
            group_openid=target.target_id, **prepare_kwargs,
        )
        part_finish = bot.post_group_upload_part_finish
        target_kwarg = {"group_openid": target.target_id}
    else:
        prepare = await bot.post_c2c_upload_prepare(
            openid=target.target_id, **prepare_kwargs,
        )
        part_finish = bot.post_c2c_upload_part_finish
        target_kwarg = {"openid": target.target_id}

    block_size = int(prepare.block_size)
    if block_size <= 0:
        raise RuntimeError("QQ image upload returned an invalid block size")
    semaphore = asyncio.Semaphore(max(1, min(4, int(prepare.upload_config.concurrency))))

    async def upload_part(offset: int, part) -> None:
        chunk = image[offset * block_size : (offset + 1) * block_size]
        if not chunk:
            raise RuntimeError("QQ image upload requested an empty part")
        async with semaphore:
            await bot.put_upload_part(presigned_url=part.presigned_url, data=chunk)
            await part_finish(
                **target_kwarg,
                upload_id=prepare.upload_id,
                part_index=part.index,
                block_size=len(chunk),
                md5=hashlib.md5(chunk).hexdigest(),
            )

    await asyncio.gather(*(
        upload_part(index, part) for index, part in enumerate(prepare.parts)
    ))
    result = await _finish_upload(bot, target, prepare.upload_id)
    raw_url = _safe_raw_url(result.get("raw_url"))
    if not raw_url:
        raise RuntimeError("QQ image upload did not return a usable raw URL")
    ttl = max(300, min(86400, int(result.get("ttl") or 3600)))
    image_url = _markdown_image_url(raw_url)
    _url_cache[key] = (time.monotonic() + max(60, ttl - URL_SAFETY_MARGIN), image_url)
    return image_url
