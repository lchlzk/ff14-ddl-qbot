from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class ToolError(ValueError):
    """A safe, user-facing validation error."""


def clean(value: str, limit: int = 100) -> str:
    value = " ".join(str(value).split())
    value = "".join(c for c in value if c.isprintable())
    if not value or len(value) > limit:
        raise ToolError(f"内容不能为空，且不能超过 {limit} 个字。")
    return value


@dataclass(frozen=True)
class Identity:
    bot: str
    scope: str
    user: str
    private: bool = False
    # Transient QQ role from the current verified group event. Never persist it
    # as a manual grant, or a demoted group administrator would retain access.
    group_role: str | None = None

    @property
    def is_group_admin(self) -> bool:
        return (
            not self.private
            and self.scope.startswith("group:")
            and self.group_role in {"admin", "owner"}
        )

    @property
    def scope_key(self) -> str:
        return hashlib.sha256(json.dumps([self.bot, self.scope]).encode()).hexdigest()

    @property
    def actor(self) -> str:
        return hashlib.sha256(json.dumps([self.bot, self.scope, self.user]).encode()).hexdigest()


class Store:
    ADMIN_TOKEN_TTL = 10 * 60
    ADMIN_CONFIRM_TTL = 15 * 60

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("BOT_DATA_DIR", "data"))
        self.path.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identities (
                    actor TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE, private INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
                    bot TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS feedback_actor ON feedback(actor, created);
                CREATE TABLE IF NOT EXISTS rates (
                    key TEXT PRIMARY KEY, last REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS rates_last ON rates(last);
                CREATE INDEX IF NOT EXISTS identities_scope ON identities(scope,private);
                CREATE TABLE IF NOT EXISTS gallery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    category TEXT NOT NULL, digest TEXT NOT NULL, image BLOB NOT NULL,
                    UNIQUE(scope,category,digest));
                CREATE INDEX IF NOT EXISTS gallery_scope_category_id ON gallery(scope, category, id);
                CREATE TABLE IF NOT EXISTS admin_tokens (
                    digest TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_bindings (
                    actor TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS group_metadata (
                    scope TEXT PRIMARY KEY,
                    official_name TEXT NOT NULL DEFAULT '',
                    manual_name TEXT NOT NULL DEFAULT '',
                    checked REAL NOT NULL DEFAULT 0,
                    retry_after REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS bot_scopes (
                    scope TEXT PRIMARY KEY,
                    bot TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_bot_scopes_bot_kind
                    ON bot_scopes(bot,kind,scope);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(gallery)")}
            for name, declaration in {
                "object_key": "TEXT NOT NULL DEFAULT ''",
                "byte_size": "INTEGER NOT NULL DEFAULT 0",
                "visibility": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE gallery ADD COLUMN {name} {declaration}")
            db.execute("CREATE INDEX IF NOT EXISTS gallery_object_key ON gallery(object_key)")
            db.execute("CREATE INDEX IF NOT EXISTS gallery_visibility ON gallery(visibility,scope,category,id)")
            feedback_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(feedback)")
            }
            if "bot" not in feedback_columns:
                db.execute("ALTER TABLE feedback ADD COLUMN bot TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_bot_closed ON feedback(bot,closed,id)")

    def document(self, scope: str) -> dict:
        """Read configuration without taking SQLite's single writer lock."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM documents WHERE key=?", (scope,)).fetchone()
            return json.loads(row[0]) if row else {}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path / "bot.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        db.create_function("casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def state(self, scope: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM documents WHERE key=?", (scope,)).fetchone()
            doc = json.loads(row[0]) if row else {}
            original = json.dumps(doc, ensure_ascii=False)
            yield doc
            updated = json.dumps(doc, ensure_ascii=False)
            if original != updated:
                db.execute("INSERT OR REPLACE INTO documents VALUES (?,?)", (scope, updated))

    def register(self, who: Identity) -> dict:
        with self.connect() as db:
            db.execute(
                "INSERT INTO bot_scopes(scope,bot,kind,updated) VALUES(?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET bot=excluded.bot,kind=excluded.kind,updated=excluded.updated",
                (who.scope_key, who.bot, "private" if who.private else
                 "group" if who.scope.startswith("group:") else "channel", time.time()),
            )
            existing = db.execute("SELECT * FROM identities WHERE actor=?", (who.actor,)).fetchone()
            if existing:
                return dict(existing)
            db.execute("INSERT OR IGNORE INTO identities(actor,scope,code,private,created) VALUES(?,?,?,?,?)",
                       (who.actor, who.scope_key, secrets.token_hex(8), int(who.private), time.time()))
            return dict(db.execute("SELECT * FROM identities WHERE actor=?", (who.actor,)).fetchone())

    def remember_scope(self, who: Identity, now: float | None = None) -> None:
        """Associate an irreversible scope digest with its bot for admin filtering."""
        kind = "private" if who.private else (
            "group" if who.scope.startswith("group:") else "channel"
        )
        with self.connect() as db:
            db.execute(
                "INSERT INTO bot_scopes(scope,bot,kind,updated) VALUES(?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET bot=excluded.bot,kind=excluded.kind,updated=excluded.updated",
                (who.scope_key, who.bot, kind, time.time() if now is None else now),
            )

    def create_admin_token(self, now: float | None = None) -> str:
        """Server CLI only. Store a hash, never the one-time bearer secret."""
        now = time.time() if now is None else now
        token = secrets.token_hex(16)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # One outstanding invitation. Regenerating invalidates the old one.
            db.execute("DELETE FROM admin_tokens")
            db.execute("DELETE FROM admin_bindings WHERE expires<=?", (now,))
            db.execute("INSERT INTO admin_tokens VALUES (?,?)", (digest, now + self.ADMIN_TOKEN_TTL))
        return token

    def redeem_admin_token(self, who: Identity, token: str, now: float | None = None) -> dict:
        if not who.private or not who.scope.startswith("private:"):
            raise ToolError("授权码仅能在机器人私聊使用，群内不会兑换。")
        now = time.time() if now is None else now
        self.throttle("admin-bind:" + who.actor, 3, now)
        token = token.lower()
        invalid = "授权码无效、已使用或已过期。"
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ToolError(invalid)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT expires FROM admin_tokens WHERE digest=?", (digest,)).fetchone()
            if not row or row["expires"] <= now:
                raise ToolError(invalid)
            db.execute("DELETE FROM admin_tokens WHERE digest=?", (digest,))
            # Rotate even legacy public codes; possession of an old code must
            # never satisfy this new private-chat verification step.
            code = secrets.token_hex(8)
            db.execute("""INSERT INTO identities(actor,scope,code,private,created)
                VALUES(?,?,?,?,?) ON CONFLICT(actor) DO UPDATE SET code=excluded.code""",
                (who.actor, who.scope_key, code, 1, now))
            db.execute("INSERT OR REPLACE INTO admin_bindings VALUES (?,?)",
                       (who.actor, now + self.ADMIN_CONFIRM_TTL))
            return dict(db.execute("SELECT * FROM identities WHERE actor=?", (who.actor,)).fetchone())

    def role(self, who: Identity) -> str:
        with self.connect() as db:
            row = db.execute("SELECT role FROM identities WHERE actor=?", (who.actor,)).fetchone()
        return row[0] if row else "member"

    def is_admin(self, who: Identity, *, owner: bool = False) -> bool:
        role = self.role(who)
        # owner=True is exclusively the server-authorized bot superadmin.
        # QQ's group role "owner" MUST NOT grant this privilege.
        return role == "owner" or (not owner and (role == "admin" or who.is_group_admin))

    def require_admin(self, who: Identity, *, owner: bool = False):
        if not self.is_admin(who, owner=owner):
            if owner:
                raise ToolError("此操作仅限服务器授权的机器人总管理员，QQ群主/管理员没有此权限。")
            if not who.private and who.scope.startswith("group:"):
                raise ToolError("此操作仅限本群群主/管理员，或总管理员明确授权的机器人管理员。以 QQ 当前消息提供的群角色为准。")
            raise ToolError("无权访问此会话的管理功能。")

    def grant(self, code: str, role: str, scope: str | None = None):
        if role not in {"member", "admin", "owner"}:
            raise ToolError("无效角色。")
        if scope is not None and role == "owner":
            raise ToolError("不能通过群内授权创建总管理员，请在服务器运行授权命令。")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM identities WHERE code=?", (code.lower(),)).fetchone()
            if not row or (scope is not None and row["scope"] != scope):
                raise ToolError("找不到此身份码，或身份码不属于目标会话。")
            if scope is not None and row["role"] == "owner":
                raise ToolError("服务器主人只能通过服务器命令修改。")
            if role == "owner":
                pending = db.execute("SELECT expires FROM admin_bindings WHERE actor=?", (row["actor"],)).fetchone()
                if not row["private"] or not pending or pending["expires"] <= time.time():
                    raise ToolError("没有有效的私聊绑定申请。请先在服务器执行 admin token，再私聊兑换并确认。")
            db.execute("UPDATE identities SET role=? WHERE code=?", (role, code.lower()))
            # Confirmation is also one-use; revoking a role cancels any pending
            # request for that identity rather than allowing it to be replayed.
            db.execute("DELETE FROM admin_bindings WHERE actor=?", (row["actor"],))

    def throttle(self, key: str, seconds: float = 2, now: float | None = None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last FROM rates WHERE key=?", (key,)).fetchone()
            if row and now - row[0] < seconds:
                raise ToolError(f"操作太快，请等 {max(1, int(seconds - (now - row[0])))} 秒再试。")
            db.execute("INSERT OR REPLACE INTO rates VALUES (?,?)", (key, now))
            db.execute("DELETE FROM rates WHERE last<?", (now - 86400,))

    def feedback_add(self, who: Identity, body: str, now: float | None = None) -> int:
        body = clean(body, 500)
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM feedback WHERE actor=? AND created>?", (who.actor, now-86400)).fetchone()[0] >= 5:
                raise ToolError("24 小时内最多提交 5 条反馈，请等待管理员处理。")
            if db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] >= 10000:
                raise ToolError("反馈箱已满，请联系服务器管理员归档。")
            return db.execute(
                "INSERT INTO feedback(actor,body,created,bot) VALUES(?,?,?,?)",
                (who.actor, body, now, who.bot),
            ).lastrowid

    def feedback_list(self, who: Identity, page: int, closed: bool = False) -> list[dict]:
        self.require_inbox(who)
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,body,created,closed FROM feedback WHERE closed=? AND bot=? "
                "ORDER BY id DESC LIMIT 4 OFFSET ?",
                (int(closed), who.bot, (page - 1) * 4),
            )]

    def require_inbox(self, who: Identity):
        self.require_admin(who, owner=True)
        if not who.private:
            raise ToolError("反馈涉及隐私，只能在已授权的机器人私聊中查看和处理。私聊也需要单独授权。")

    def feedback_close(self, who: Identity, item_id: int):
        self.require_inbox(who)
        with self.connect() as db:
            if not db.execute(
                "UPDATE feedback SET closed=1 WHERE id=? AND bot=?", (item_id, who.bot)
            ).rowcount:
                raise ToolError("没有这条反馈。")
