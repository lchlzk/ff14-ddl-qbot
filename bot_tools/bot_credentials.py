"""Encrypted, admin-managed QQ bot credentials and runtime configuration."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

from .storage import Store, ToolError, clean


BOT_ID = re.compile(r"[A-Za-z0-9._~-]{1,128}")
MAX_BOTS = 20


@dataclass(frozen=True)
class RuntimeBot:
    app_id: str
    secret: str = field(repr=False)
    label: str
    connection: str
    c2c_group_at_messages: bool
    at_messages: bool

    def adapter_config(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.app_id,
            "token": "",
            "secret": self.secret,
            "use_websocket": self.connection == "websocket",
        }
        if self.connection == "websocket":
            result["intent"] = {
                "c2c_group_at_messages": self.c2c_group_at_messages,
                "at_messages": self.at_messages,
            }
        return result


class BotCredentialStore:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS qq_bot_credentials (
                    app_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    secret BLOB NOT NULL,
                    connection TEXT NOT NULL DEFAULT 'websocket',
                    c2c_group_at_messages INTEGER NOT NULL DEFAULT 1,
                    at_messages INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_qq_bot_credentials_enabled
                    ON qq_bot_credentials(enabled,app_id);
            """)

    def _vault(self, db) -> Fernet:
        folder = self.store.path / "secrets"
        path = folder / "qq-bots-master.key"
        if folder.is_symlink() or path.is_symlink():
            raise ToolError("机器人凭据加密文件路径异常，请检查数据目录。")
        folder.mkdir(mode=0o700, exist_ok=True)
        if not path.exists():
            if db.execute("SELECT 1 FROM qq_bot_credentials LIMIT 1").fetchone():
                raise ToolError("机器人凭据主密钥丢失，请从备份恢复后再启动。")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(Fernet.generate_key())
                output.flush()
                os.fsync(output.fileno())
        try:
            key = path.read_bytes()
            if len(key) != 44:
                raise ValueError("wrong key length")
            return Fernet(key)
        except (OSError, ValueError) as exc:
            raise ToolError("无法读取机器人凭据主密钥，请检查数据目录权限。") from exc

    @staticmethod
    def _app_id(value: object) -> str:
        app_id = str(value or "").strip()
        if not BOT_ID.fullmatch(app_id):
            raise ToolError("AppID 格式不正确。")
        return app_id

    @staticmethod
    def _secret(value: object) -> str:
        secret = str(value or "")
        if not 1 <= len(secret) <= 512 or any(char in secret for char in "\0\r\n"):
            raise ToolError("AppSecret 格式不正确。")
        return secret

    def save(
        self,
        app_id: object,
        secret: object,
        *,
        label: object = "",
        connection: object = "websocket",
        c2c_group_at_messages: object = True,
        at_messages: object = True,
        enabled: object = True,
        now: float | None = None,
    ) -> dict:
        app_id = self._app_id(app_id)
        label = str(label or "").strip()
        label = clean(label, 40) if label else app_id
        connection = str(connection or "").strip().lower()
        if connection not in {"websocket", "webhook"}:
            raise ToolError("连接方式只能是 websocket 或 webhook。")
        if type(c2c_group_at_messages) is not bool or type(at_messages) is not bool:
            raise ToolError("消息事件开关格式不正确。")
        if type(enabled) is not bool:
            raise ToolError("机器人启用状态格式不正确。")
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT secret,created FROM qq_bot_credentials WHERE app_id=?", (app_id,)
            ).fetchone()
            if row is None and db.execute("SELECT COUNT(*) FROM qq_bot_credentials").fetchone()[0] >= MAX_BOTS:
                raise ToolError(f"最多管理 {MAX_BOTS} 个机器人。")
            if str(secret or ""):
                encrypted = self._vault(db).encrypt(self._secret(secret).encode("utf-8"))
            elif row is not None:
                encrypted = row["secret"]
            else:
                raise ToolError("新增机器人时必须填写 AppSecret。")
            created = float(row["created"]) if row is not None else now
            db.execute(
                """INSERT INTO qq_bot_credentials
                   (app_id,label,secret,connection,c2c_group_at_messages,at_messages,enabled,created,updated)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(app_id) DO UPDATE SET
                   label=excluded.label,secret=excluded.secret,connection=excluded.connection,
                   c2c_group_at_messages=excluded.c2c_group_at_messages,
                   at_messages=excluded.at_messages,enabled=excluded.enabled,updated=excluded.updated""",
                (app_id, label, encrypted, connection, int(c2c_group_at_messages),
                 int(at_messages), int(enabled), created, now),
            )
        return {"app_id": app_id, "created": row is None}

    def import_config(self, config: dict, *, now: float | None = None) -> bool:
        """Import a legacy environment entry once without overwriting admin changes."""
        try:
            app_id = self._app_id(config.get("id"))
        except ToolError:
            return False
        with self.store.connect() as db:
            if db.execute(
                "SELECT 1 FROM qq_bot_credentials WHERE app_id=?", (app_id,)
            ).fetchone():
                return False
        secret = config.get("secret")
        if not isinstance(secret, str) or not secret:
            return False
        intent = config.get("intent") if isinstance(config.get("intent"), dict) else {}
        self.save(
            app_id, secret, label=app_id,
            connection="websocket" if config.get("use_websocket", True) else "webhook",
            c2c_group_at_messages=bool(intent.get("c2c_group_at_messages", True)),
            at_messages=bool(intent.get("at_messages", True)), enabled=True, now=now,
        )
        return True

    def metadata(self) -> list[dict]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT app_id,label,connection,c2c_group_at_messages,at_messages,enabled,created,updated "
                "FROM qq_bot_credentials ORDER BY label,app_id"
            ).fetchall()
        return [{
            "app_id": row["app_id"], "label": row["label"],
            "connection": row["connection"],
            "c2c_group_at_messages": bool(row["c2c_group_at_messages"]),
            "at_messages": bool(row["at_messages"]), "enabled": bool(row["enabled"]),
            "secret_configured": True, "created": row["created"], "updated": row["updated"],
        } for row in rows]

    def runtime(self) -> list[RuntimeBot]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM qq_bot_credentials WHERE enabled=1 ORDER BY app_id"
            ).fetchall()
            if not rows:
                return []
            vault = self._vault(db)
            result = []
            for row in rows:
                try:
                    secret = vault.decrypt(bytes(row["secret"])).decode("utf-8")
                except (InvalidToken, UnicodeError) as exc:
                    raise ToolError(
                        f"机器人 {row['app_id']} 的加密凭据无法读取，请从后台重新填写。"
                    ) from exc
                result.append(RuntimeBot(
                    row["app_id"], secret, row["label"], row["connection"],
                    bool(row["c2c_group_at_messages"]), bool(row["at_messages"]),
                ))
            return result

    def runtime_one(self, app_id: object) -> RuntimeBot | None:
        """Decrypt one enabled bot for an in-process connection refresh."""
        app_id = self._app_id(app_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM qq_bot_credentials WHERE app_id=? AND enabled=1",
                (app_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                secret = self._vault(db).decrypt(bytes(row["secret"])).decode("utf-8")
            except (InvalidToken, UnicodeError) as exc:
                raise ToolError(
                    f"机器人 {row['app_id']} 的加密凭据无法读取，请从后台重新填写。"
                ) from exc
        return RuntimeBot(
            row["app_id"], secret, row["label"], row["connection"],
            bool(row["c2c_group_at_messages"]), bool(row["at_messages"]),
        )

    def delete(self, app_id: object) -> bool:
        app_id = self._app_id(app_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return bool(db.execute(
                "DELETE FROM qq_bot_credentials WHERE app_id=?", (app_id,)
            ).rowcount)


def parse_legacy_environment() -> list[dict]:
    """Read old plaintext variables for one-time migration; never log values."""
    app_id = os.environ.get("QQ_APP_ID", "").strip()
    app_secret = os.environ.get("QQ_APP_SECRET", "").strip()
    if app_id or app_secret:
        if not app_id or not app_secret:
            raise RuntimeError("QQ_APP_ID and QQ_APP_SECRET must both be set")
        connection = os.environ.get("QQ_CONNECTION", "websocket").strip().lower()
        if connection not in {"websocket", "webhook"}:
            raise RuntimeError("QQ_CONNECTION must be websocket or webhook")
        return [{
            "id": app_id, "secret": app_secret, "token": "",
            "use_websocket": connection == "websocket",
            "intent": {
                "c2c_group_at_messages": _environment_bool("QQ_C2C_GROUP_AT_MESSAGES", True),
                "at_messages": _environment_bool("QQ_AT_MESSAGES", True),
            },
        }]
    raw = os.environ.get("QQ_BOTS", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("QQ_BOTS must be valid JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError("QQ_BOTS must be a JSON array")
    return value


def _environment_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def configure_runtime(store: Store | None = None) -> list[RuntimeBot]:
    """Migrate old env credentials and decrypt enabled entries for this process only."""
    store = store or Store()
    credentials = BotCredentialStore(store)
    legacy = parse_legacy_environment()
    for item in legacy:
        credentials.import_config(item)
    bots = credentials.runtime()
    revisions = {
        item["app_id"]: item["updated"] for item in credentials.metadata()
    }
    os.environ["QQBOT_ACTIVE_BOT_IDS"] = json.dumps([bot.app_id for bot in bots])
    os.environ["QQBOT_ACTIVE_BOT_REVISIONS"] = json.dumps(revisions, separators=(",", ":"))
    os.environ["QQBOT_LEGACY_CREDENTIALS_PRESENT"] = "true" if legacy else "false"
    os.environ.pop("QQ_APP_ID", None)
    os.environ.pop("QQ_APP_SECRET", None)
    os.environ.pop("QQ_BOTS", None)
    return bots
