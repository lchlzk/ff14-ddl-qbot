"""Private content-addressed files; no paths or object keys are public URLs."""
from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image

from .storage import ToolError


class ObjectBackend(Protocol):
    def put(self, data: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def thumbnail(self, data: bytes) -> bytes: ...


KEY = re.compile(r"[a-f0-9]{64}")
MAX_OBJECT_BYTES = 32 * 1024 * 1024


def _key(value: str) -> str:
    if not KEY.fullmatch(value):
        raise ToolError("图片存储标识无效。")
    return value


def _thumbnail_bytes(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.seek(0)  # Preview only; the animated original is unchanged.
            source.thumbnail((360, 360))
            rgba = source.convert("RGBA")
            result = Image.new("RGB", rgba.size, "white")
            result.paste(rgba, mask=rgba.getchannel("A"))
            output = io.BytesIO()
            result.save(output, "JPEG", quality=75, optimize=True)
        return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ToolError("图片无法生成预览，请查看原图。") from None


class LocalObjects:
    def __init__(self, data_dir: Path):
        self.root = data_dir.resolve() / "media"
        if self.root.is_symlink() or not self.root.resolve().is_relative_to(data_dir.resolve()):
            raise ToolError("媒体目录不能链接到数据目录之外。")

    def path(self, key: str, *, thumbnail=False) -> Path:
        key = _key(key)
        base = self.root / ("thumbnails" if thumbnail else "objects")
        path = base / key[:2] / (key + (".jpg" if thumbnail else ""))
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ToolError("图片存储路径不安全。")
        return path

    @staticmethod
    def _write(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free < len(data) + 64 * 1024**2:
            raise ToolError("服务器磁盘空间不足，已停止新增图片；现有图片不会被自动删除。")
        descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        path = self.path(key)
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != key:
            self._write(path, data)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self.path(key).read_bytes()
        except OSError:
            raise ToolError("图片原文件缺失或暂时不可读，请管理员检查媒体目录或备份。") from None

    def thumbnail(self, data: bytes) -> bytes:
        key = hashlib.sha256(data).hexdigest()
        path = self.path(key, thumbnail=True)
        if path.exists():
            return path.read_bytes()
        preview = _thumbnail_bytes(data)
        self._write(path, preview)
        return preview


@dataclass(frozen=True)
class CosSettings:
    bucket: str
    region: str
    prefix: str = "qbot/media"
    secret_id: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    local_fallback: bool = True


def _secret_file(variable: str) -> str:
    raw = os.environ.get(variable, "").strip()
    if not raw:
        raise ToolError(f"尚未配置 {variable}。")
    path = Path(raw)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ToolError(f"{variable} 必须指向容器内的普通密钥文件。")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ToolError(f"无法读取 {variable}。") from exc
    if not value or len(value) > 512 or any(char in value for char in "\0\r\n"):
        raise ToolError(f"{variable} 的密钥格式不正确。")
    return value


def cos_settings_from_environment() -> CosSettings:
    bucket = os.environ.get("COS_BUCKET", "").strip().lower()
    region = os.environ.get("COS_REGION", "").strip().lower()
    prefix = os.environ.get("COS_PREFIX", "qbot/media").strip().strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}-[0-9]{5,20}", bucket):
        raise ToolError("COS_BUCKET 必须是包含 APPID 的完整桶名，例如 qbot-1250000000。")
    if not re.fullmatch(r"[a-z]{2,8}-[a-z0-9-]{2,32}", region):
        raise ToolError("COS_REGION 格式不正确，例如 ap-guangzhou。")
    if (not prefix or len(prefix) > 512
            or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) or part in {".", ".."}
                   for part in prefix.split("/"))):
        raise ToolError("COS_PREFIX 只能包含安全的目录片段。")
    fallback = os.environ.get("COS_LOCAL_FALLBACK", "true").strip().lower()
    if fallback not in {"true", "false"}:
        raise ToolError("COS_LOCAL_FALLBACK 只能是 true 或 false。")
    return CosSettings(
        bucket=bucket,
        region=region,
        prefix=prefix,
        secret_id=_secret_file("COS_SECRET_ID_FILE"),
        secret_key=_secret_file("COS_SECRET_KEY_FILE"),
        local_fallback=fallback == "true",
    )


class CosObjects:
    """Private Tencent COS objects with an optional read-only local fallback."""

    def __init__(self, data_dir: Path, settings: CosSettings | None = None, *,
                 client=None, errors: tuple[type[BaseException], ...] | None = None):
        self.settings = settings or cos_settings_from_environment()
        self.local = LocalObjects(data_dir)
        if client is None:
            try:
                from qcloud_cos import CosConfig, CosS3Client
                from qcloud_cos.cos_exception import CosClientError, CosServiceError
            except ImportError as exc:
                raise ToolError("服务器尚未安装腾讯云 COS Python SDK。") from exc
            config = CosConfig(
                Region=self.settings.region,
                SecretId=self.settings.secret_id,
                SecretKey=self.settings.secret_key,
                Scheme="https",
                Timeout=15,
            )
            client = CosS3Client(config)
            errors = (CosClientError, CosServiceError)
        self.client = client
        self.errors = errors or (Exception,)

    def _object_key(self, key: str, *, thumbnail=False) -> str:
        key = _key(key)
        folder = "thumbnails" if thumbnail else "objects"
        suffix = ".jpg" if thumbnail else ""
        return f"{self.settings.prefix}/{folder}/{key[:2]}/{key}{suffix}"

    @staticmethod
    def _missing(exc: BaseException) -> bool:
        status_method = getattr(exc, "get_status_code", None)
        code_method = getattr(exc, "get_error_code", None)
        status = status_method() if callable(status_method) else None
        code = code_method() if callable(code_method) else None
        return str(status) == "404" or code in {"NoSuchKey", "NoSuchObject"}

    @staticmethod
    def _message(action: str) -> ToolError:
        return ToolError(f"腾讯云 COS {action}失败，请管理员检查桶权限、地域和网络。")

    def _head(self, key: str, *, thumbnail=False) -> dict | None:
        try:
            return self.client.head_object(
                Bucket=self.settings.bucket,
                Key=self._object_key(key, thumbnail=thumbnail),
            )
        except self.errors as exc:
            if self._missing(exc):
                return None
            raise self._message("读取对象信息") from exc

    def _put(self, key: str, data: bytes, *, thumbnail=False) -> None:
        if not data or len(data) > MAX_OBJECT_BYTES:
            raise ToolError("图片对象大小不正确。")
        head = self._head(key, thumbnail=thumbnail)
        if head is not None and int(head.get("Content-Length", -1)) == len(data):
            return
        try:
            self.client.put_object(
                Bucket=self.settings.bucket,
                Key=self._object_key(key, thumbnail=thumbnail),
                Body=data,
                EnableMD5=True,
                ContentType="image/jpeg" if thumbnail else "application/octet-stream",
                CacheControl="private,max-age=31536000,immutable",
                Metadata={"sha256": hashlib.sha256(data).hexdigest()},
            )
        except self.errors as exc:
            raise self._message("上传") from exc
        head = self._head(key, thumbnail=thumbnail)
        if head is None or int(head.get("Content-Length", -1)) != len(data):
            raise ToolError("腾讯云 COS 上传后的大小校验失败，本次图片未写入数据库。")

    def _read_cos(self, key: str, *, thumbnail=False) -> bytes:
        try:
            response = self.client.get_object(
                Bucket=self.settings.bucket,
                Key=self._object_key(key, thumbnail=thumbnail),
            )
            length = int(response.get("Content-Length", -1))
            if length > MAX_OBJECT_BYTES:
                raise ToolError("腾讯云 COS 图片对象超过允许大小。")
            stream = response["Body"].get_raw_stream()
            try:
                data = stream.read(MAX_OBJECT_BYTES + 1)
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
        except ToolError:
            raise
        except self.errors as exc:
            if self._missing(exc):
                raise FileNotFoundError from None
            raise self._message("下载") from exc
        if not data or len(data) > MAX_OBJECT_BYTES:
            raise ToolError("腾讯云 COS 图片对象为空或超过允许大小。")
        return data

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        self._put(key, data)
        return key

    def read(self, key: str) -> bytes:
        key = _key(key)
        try:
            data = self._read_cos(key)
        except FileNotFoundError:
            if not self.settings.local_fallback:
                raise ToolError("腾讯云 COS 中缺少图片原文件。") from None
            return self.local.read(key)
        if hashlib.sha256(data).hexdigest() != key:
            raise ToolError("腾讯云 COS 图片内容校验失败。")
        return data

    def thumbnail(self, data: bytes) -> bytes:
        key = hashlib.sha256(data).hexdigest()
        try:
            return self._read_cos(key, thumbnail=True)
        except FileNotFoundError:
            if self.settings.local_fallback:
                path = self.local.path(key, thumbnail=True)
                if path.is_file():
                    return path.read_bytes()
        preview = _thumbnail_bytes(data)
        self._put(key, preview, thumbnail=True)
        return preview

    def verify_round_trip(self) -> None:
        data = b"qbot-cos-check:" + secrets.token_bytes(32)
        key = hashlib.sha256(data).hexdigest()
        self._put(key, data)
        try:
            if self._read_cos(key) != data:
                raise ToolError("腾讯云 COS 上传下载校验失败。")
        finally:
            try:
                self.client.delete_object(
                    Bucket=self.settings.bucket,
                    Key=self._object_key(key),
                )
            except self.errors as exc:
                raise self._message("删除校验对象") from exc


@lru_cache(maxsize=8)
def _cos_backend(data_dir: str, settings: CosSettings) -> CosObjects:
    return CosObjects(Path(data_dir), settings)


def configured_objects(data_dir: Path,
                       local_factory: Callable[[Path], ObjectBackend] = LocalObjects) -> ObjectBackend:
    mode = os.environ.get("MEDIA_OBJECT_BACKEND", "local").strip().lower()
    if mode == "local":
        return local_factory(data_dir)
    if mode != "cos":
        raise ToolError("MEDIA_OBJECT_BACKEND 只能是 local 或 cos。")
    settings = cos_settings_from_environment()
    return _cos_backend(str(data_dir.resolve()), settings)


def migrate_local_to_cos(data_dir: Path) -> tuple[int, int]:
    """Copy and fully verify local originals/thumbnails without deleting either."""
    backend = configured_objects(data_dir)
    if not isinstance(backend, CosObjects):
        raise ToolError("请先把 MEDIA_OBJECT_BACKEND 设置为 cos。")
    local = LocalObjects(data_dir)
    originals = thumbnails = 0
    for path in sorted((local.root / "objects").glob("*/*")):
        key = _key(path.name)
        data = path.read_bytes()
        backend._put(key, data)
        if backend._read_cos(key) != data:
            raise ToolError(f"COS 原图迁移校验失败：{key}")
        originals += 1
    for path in sorted((local.root / "thumbnails").glob("*/*.jpg")):
        key = _key(path.stem)
        data = path.read_bytes()
        backend._put(key, data, thumbnail=True)
        if backend._read_cos(key, thumbnail=True) != data:
            raise ToolError(f"COS 缩略图迁移校验失败：{key}")
        thumbnails += 1
    return originals, thumbnails


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Tencent COS media maintenance")
    parser.add_argument("action", choices=("check-cos", "migrate-cos"))
    args = parser.parse_args()
    data_dir = Path(os.environ.get("BOT_DATA_DIR", "data"))
    backend = configured_objects(data_dir)
    if not isinstance(backend, CosObjects):
        raise ToolError("请先把 MEDIA_OBJECT_BACKEND 设置为 cos。")
    if args.action == "check-cos":
        backend.verify_round_trip()
        print(f"COS 检查通过：{backend.settings.bucket}/{backend.settings.prefix}")
    else:
        originals, thumbnails = migrate_local_to_cos(data_dir)
        print(f"COS 迁移并校验完成：原图 {originals}，缩略图 {thumbnails}；本地文件未删除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
