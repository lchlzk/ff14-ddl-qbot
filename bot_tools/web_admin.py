"""Password-protected, same-origin administration UI for the local QQ bot.

The browser never receives API credentials, raw QQ OpenIDs, role owners or
personas. The single administrator account stores only a salted password hash;
password setup and recovery require local access to the server launcher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .ai_provider import model_for
from .ai_store import AIStore, conversation
from .bot_credentials import BOT_ID, BotCredentialStore
from .community import MANAGED
from .learning_chat import LearningStore
from .storage import Store, ToolError, clean
from .gallery import SIZE_SQL, read_row
from .media_objects import LocalObjects, configured_objects


WEB_ROOT = Path(__file__).resolve().parents[1] / "admin_web"
COOKIE = "qqbot_admin_session"
SESSION_TTL = 12 * 60 * 60
LOGIN_WINDOW = 5 * 60
LOGIN_LIMIT = 5
PASSWORD_ROUNDS = 310_000
PAGE_SIZE = 24
STARTED_AT = time.time()
HEX64 = re.compile(r"[a-f0-9]{64}")
SCOPE = re.compile(r"[a-f0-9]{64}")
GROUP_PLUGIN_SWITCHES = {"ff14": "FF14", "tr": "嘟嘟脸", "bili": "B站推送"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password_hash(password: str, salt: str, rounds: int = PASSWORD_ROUNDS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds,
    ).hex()


def _username(value: object) -> tuple[str, str]:
    username = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not 2 <= len(username) <= 32 or not re.fullmatch(r"[\w.@-]+", username):
        raise ToolError("用户名应为 2～32 位中文、字母、数字或 . _ @ -。")
    return username, username.casefold()


def _validate_password(username: str, password: object) -> str:
    password = str(password or "")
    if not 10 <= len(password) <= 128 or any(char in password for char in "\0\r\n"):
        raise ToolError("密码长度应为 10～128 个字符，不能包含换行。")
    if password.casefold() == username.casefold() or len(set(password)) < 4:
        raise ToolError("密码过于简单，请不要使用用户名或重复字符。")
    return password


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    if value < 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MiB"
    return f"{value / (1024 * 1024 * 1024):.2f} GiB"


def _page(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ToolError("页码格式不正确。") from None
    if not 1 <= result <= 10000:
        raise ToolError("页码范围是 1～10000。")
    return result


def _query(value: object, *, limit: int = 60) -> str:
    result = " ".join(str(value or "").split())
    if len(result) > limit:
        raise ToolError(f"搜索内容不能超过 {limit} 个字符。")
    return result.casefold()


class WebAdmin:
    def __init__(self, store: Store, bot_runtime: Any = None):
        self.store = store
        self.credentials = BotCredentialStore(store)
        self.bot_runtime = bot_runtime
        # Ensure every feature table exists even before its first QQ command.
        self.ai = AIStore(store)
        LearningStore(store)
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS web_login_tokens (
                    digest TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS web_accounts (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    iterations INTEGER NOT NULL,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS web_sessions (
                    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_web_sessions_expires
                    ON web_sessions(expires);
                CREATE TABLE IF NOT EXISTS web_login_attempts (
                    client TEXT PRIMARY KEY, started REAL NOT NULL,
                    failures INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS web_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL, target TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_web_audit_created
                    ON web_audit(created DESC);
                CREATE TABLE IF NOT EXISTS group_metadata (
                    scope TEXT PRIMARY KEY,
                    official_name TEXT NOT NULL DEFAULT '',
                    manual_name TEXT NOT NULL DEFAULT '',
                    checked REAL NOT NULL DEFAULT 0,
                    retry_after REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '');
            """)
            db.execute(
                "INSERT OR IGNORE INTO bot_scopes(scope,bot,kind,updated) "
                "SELECT scope,bot,'group',? FROM ai_groups WHERE bot<>''",
                (time.time(),),
            )
            known_bots = {item["app_id"] for item in self.credentials.metadata()}
            known_bots.update(row[0] for row in db.execute(
                "SELECT DISTINCT bot FROM ai_profiles WHERE bot<>''"
            ))
            known_bots.update(row[0] for row in db.execute(
                "SELECT DISTINCT bot FROM bot_scopes WHERE bot<>''"
            ))
            if len(known_bots) == 1:
                legacy_bot = next(iter(known_bots))
                db.execute(
                    "INSERT OR IGNORE INTO bot_scopes(scope,bot,kind,updated) "
                    "SELECT scope,?,'group',? FROM group_metadata",
                    (legacy_bot, time.time()),
                )
                db.execute("UPDATE feedback SET bot=? WHERE bot=''", (legacy_bot,))

    @staticmethod
    def _active_bot_ids() -> set[str]:
        try:
            value = json.loads(os.environ.get("QQBOT_ACTIVE_BOT_IDS", "[]"))
        except json.JSONDecodeError:
            return set()
        return {str(item) for item in value if isinstance(item, str)} if isinstance(value, list) else set()

    @staticmethod
    def _active_bot_revisions() -> dict[str, float]:
        try:
            value = json.loads(os.environ.get("QQBOT_ACTIVE_BOT_REVISIONS", "{}"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(value, dict):
            return {}
        result = {}
        for app_id, updated in value.items():
            if isinstance(app_id, str) and isinstance(updated, (int, float)):
                result[app_id] = float(updated)
        return result

    def _known_bot_ids(self, db: sqlite3.Connection) -> set[str]:
        result = {item["app_id"] for item in self.credentials.metadata()}
        result.update(self._active_bot_ids())
        result.update(row[0] for row in db.execute(
            "SELECT DISTINCT bot FROM bot_scopes WHERE bot<>''"
        ))
        result.update(row[0] for row in db.execute(
            "SELECT DISTINCT bot FROM ai_profiles WHERE bot<>''"
        ))
        return result

    def _select_bot(self, db: sqlite3.Connection, requested: str = "") -> str:
        requested = str(requested or "").strip()
        known = self._known_bot_ids(db)
        if requested:
            if not BOT_ID.fullmatch(requested) or requested not in known:
                raise ToolError("没有找到这个机器人。")
            return requested
        active = sorted(self._active_bot_ids() & known)
        if active:
            return active[0]
        return sorted(known)[0] if known else ""

    def bots(self) -> dict:
        runtime = self.bot_runtime.snapshot() if self.bot_runtime is not None else None
        active = set(runtime["loaded"]) if runtime is not None else self._active_bot_ids()
        connected = set(runtime["connected"]) if runtime is not None else set(active)
        connecting = set(runtime["connecting"]) if runtime is not None else set()
        webhook = set(runtime["webhook"]) if runtime is not None else set()
        revisions = dict(runtime["revisions"]) if runtime is not None else self._active_bot_revisions()
        rows = self.credentials.metadata()
        with self.store.connect() as db:
            data_counts = {row[0]: row[1] for row in db.execute(
                "SELECT bot,COUNT(*) FROM bot_scopes WHERE kind='group' GROUP BY bot"
            )}
        enabled = {row["app_id"] for row in rows if row["enabled"]}
        for row in rows:
            row["loaded"] = row["app_id"] in active
            row["connected"] = row["app_id"] in connected
            row["groups"] = data_counts.get(row["app_id"], 0)
            row["restart_required"] = (
                row["loaded"] != row["enabled"]
                or float(row["updated"]) > revisions.get(row["app_id"], 0)
            )
            if not row["enabled"]:
                row["state"] = "disabled"
            elif not row["loaded"]:
                row["state"] = "pending"
            elif row["app_id"] in webhook:
                row["state"] = "listening"
            elif row["connected"]:
                row["state"] = "connected"
            elif row["app_id"] in connecting:
                row["state"] = "connecting"
            else:
                row["state"] = "error"
        return {
            "items": rows,
            "active": sorted(active),
            "restart_required": active != enabled or any(row["restart_required"] for row in rows),
            "legacy_plaintext": os.environ.get("QQBOT_LEGACY_CREDENTIALS_PRESENT") == "true",
        }

    def save_bot(self, data: dict) -> dict:
        result = self.credentials.save(
            data.get("app_id"), data.get("secret", ""), label=data.get("label", ""),
            connection=data.get("connection", "websocket"),
            c2c_group_at_messages=data.get("c2c_group_at_messages", True),
            at_messages=data.get("at_messages", True), enabled=data.get("enabled", True),
        )
        with self.store.connect() as db:
            self._audit(db, "保存机器人", str(result["app_id"]))
        return result

    def delete_bot(self, app_id: str) -> bool:
        deleted = self.credentials.delete(app_id)
        if not deleted:
            raise ToolError("没有找到这个机器人。")
        with self.store.connect() as db:
            self._audit(db, "删除机器人凭据", app_id)
        return True

    @staticmethod
    def _purge(db: sqlite3.Connection, now: float) -> None:
        # web_login_tokens is retained only so upgrades from the former
        # one-time-code release can invalidate old pending codes safely.
        db.execute("DELETE FROM web_login_tokens")
        db.execute("DELETE FROM web_sessions WHERE expires<=?", (now,))
        db.execute("DELETE FROM web_login_attempts WHERE started<?", (now - LOGIN_WINDOW,))
        db.execute(
            "DELETE FROM web_audit WHERE id NOT IN "
            "(SELECT id FROM web_audit ORDER BY id DESC LIMIT 5000)"
        )

    def set_account(self, username: object, password: object,
                    now: float | None = None) -> dict:
        now = time.time() if now is None else now
        username, username_key = _username(username)
        password = _validate_password(username, password)
        salt = secrets.token_hex(16)
        password_hash = _password_hash(password, salt)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existed = db.execute("SELECT 1 FROM web_accounts WHERE id=1").fetchone() is not None
            revoked = db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0]
            db.execute(
                "INSERT INTO web_accounts(id,username,username_key,salt,password_hash,iterations,updated) "
                "VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "username=excluded.username,username_key=excluded.username_key,salt=excluded.salt,"
                "password_hash=excluded.password_hash,iterations=excluded.iterations,updated=excluded.updated",
                (username, username_key, salt, password_hash, PASSWORD_ROUNDS, now),
            )
            db.execute("DELETE FROM web_sessions")
            db.execute("DELETE FROM web_login_tokens")
            db.execute("DELETE FROM web_login_attempts")
            self._audit(db, "更新网页登录账号" if existed else "创建网页登录账号", "网页管理后台", now)
        return {"created": not existed, "revoked": revoked, "username": username}

    def authenticate(self, username: object, password: object, client: str,
                     now: float | None = None) -> tuple[str, str]:
        now = time.time() if now is None else now
        client_key = _digest("web-client\0" + client)
        supplied_username = unicodedata.normalize("NFKC", str(username or "")).strip().casefold()
        supplied_password = str(password or "")
        failure_message = "用户名或密码不正确。"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge(db, now)
            attempt = db.execute(
                "SELECT started,failures FROM web_login_attempts WHERE client=?", (client_key,)
            ).fetchone()
            if attempt and now - attempt["started"] < LOGIN_WINDOW and attempt["failures"] >= LOGIN_LIMIT:
                raise ToolError("登录尝试过多，请 5 分钟后再试。")
            row = db.execute(
                "SELECT username_key,salt,password_hash,iterations FROM web_accounts WHERE id=1"
            ).fetchone()
            # Reserve the attempt atomically, then release SQLite's writer
            # lock before the deliberately expensive password calculation.
            if not attempt or now - attempt["started"] >= LOGIN_WINDOW:
                db.execute("INSERT OR REPLACE INTO web_login_attempts VALUES(?,?,1)", (client_key, now))
            else:
                db.execute("UPDATE web_login_attempts SET failures=failures+1 WHERE client=?", (client_key,))
        salt = row["salt"] if row else "00" * 16
        rounds = int(row["iterations"]) if row else PASSWORD_ROUNDS
        acceptable = (isinstance(password, str) and 10 <= len(password) <= 128
                      and not any(char in password for char in "\0\r\n"))
        supplied_hash = _password_hash(supplied_password if acceptable else "", salt, rounds)
        valid = bool(acceptable and row and supplied_username == row["username_key"]
                     and secrets.compare_digest(supplied_hash, row["password_hash"]))
        if not row:
            failure_message = "尚未设置后台账号，请先在服务器运行 web account。"
        if valid:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT username_key,salt,password_hash,iterations FROM web_accounts WHERE id=1"
                ).fetchone()
                # A server-side password reset while hashing must not create
                # a fresh session using the old account/password.
                if not current or tuple(current) != tuple(row):
                    raise ToolError("后台账号已更新，请重新登录。")
                db.execute("DELETE FROM web_login_attempts WHERE client=?", (client_key,))
                session = secrets.token_hex(32)
                csrf = secrets.token_hex(24)
                db.execute(
                    "INSERT INTO web_sessions VALUES(?,?,?,?)",
                    (_digest(session), csrf, now, now + SESSION_TTL),
                )
                self._audit(db, "网页登录", "网页管理后台", now)
                return session, csrf
        raise ToolError(failure_message)

    def session(self, token: str | None, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        token = (token or "").strip().lower()
        if not HEX64.fullmatch(token):
            return None
        with self.store.connect() as db:
            row = db.execute(
                "SELECT csrf,created,expires FROM web_sessions WHERE digest=?",
                (_digest(token),),
            ).fetchone()
            if not row or row["expires"] <= now:
                if row:
                    db.execute("DELETE FROM web_sessions WHERE digest=?", (_digest(token),))
                return None
            return dict(row)

    def logout(self, token: str | None) -> None:
        token = (token or "").strip().lower()
        if HEX64.fullmatch(token):
            with self.store.connect() as db:
                db.execute("DELETE FROM web_sessions WHERE digest=?", (_digest(token),))

    def revoke_sessions(self) -> int:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0]
            db.execute("DELETE FROM web_sessions")
            db.execute("DELETE FROM web_login_tokens")
            return count

    def auth_status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge(db, now)
            account = db.execute("SELECT username FROM web_accounts WHERE id=1").fetchone()
            return {
                "sessions": db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0],
                "configured": account is not None,
                "username": account["username"] if account else "",
            }

    @staticmethod
    def _audit(db: sqlite3.Connection, action: str, target: str,
               now: float | None = None) -> None:
        db.execute(
            "INSERT INTO web_audit(action,target,created) VALUES(?,?,?)",
            (action[:80], target[:120], time.time() if now is None else now),
        )

    @staticmethod
    def _document(db: sqlite3.Connection, scope: str) -> dict:
        row = db.execute("SELECT value FROM documents WHERE key=?", (scope,)).fetchone()
        if not row:
            return {}
        try:
            value = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _save_document(db: sqlite3.Connection, scope: str, doc: dict) -> None:
        db.execute(
            "INSERT OR REPLACE INTO documents(key,value) VALUES(?,?)",
            (scope, json.dumps(doc, ensure_ascii=False, separators=(",", ":"))),
        )

    @staticmethod
    def _groups_sql() -> str:
        return """WITH scopes AS (
            SELECT scope FROM bot_scopes WHERE bot=? AND kind='group'
            UNION SELECT scope FROM ai_groups WHERE bot=?
        ) """

    def _known_groups(self, db: sqlite3.Connection, bot: str = "") -> list[str]:
        if bot:
            return [row[0] for row in db.execute(
                self._groups_sql() +
                "SELECT scope FROM scopes WHERE length(scope)=64 AND scope NOT GLOB '*[^0-9a-f]*' ORDER BY scope",
                (bot, bot),
            )]
        return [row[0] for row in db.execute(
            "SELECT scope FROM bot_scopes WHERE kind='group' "
            "UNION SELECT scope FROM ai_groups ORDER BY scope"
        )]

    def overview(self, bot: str = "") -> dict:
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
            groups = self._known_groups(db, bot)
            local_scopes = {row[0] for row in db.execute(
                "SELECT key FROM documents WHERE json_extract(CASE WHEN json_valid(value) THEN value ELSE '{}' END,'$.gallery_mode')='local'"
            )}
            local_scopes.intersection_update(groups)
            gallery_count, gallery_bytes = db.execute(
                f"SELECT COUNT(*),COALESCE(SUM({SIZE_SQL}),0) FROM gallery"
            ).fetchone()
            feedback_open = db.execute(
                "SELECT COUNT(*) FROM feedback WHERE closed=0 AND bot=?", (bot,)
            ).fetchone()[0]
            roles = db.execute("SELECT COUNT(*) FROM ai_profiles WHERE bot=?", (bot,)).fetchone()[0]
            grants = db.execute("SELECT COUNT(*) FROM ai_groups WHERE bot=?", (bot,)).fetchone()[0]
            if groups:
                marks = ",".join("?" for _ in groups)
                pairs = db.execute(
                    f"SELECT COUNT(*) FROM learning_pairs WHERE banned=0 AND scope IN ({marks})",
                    groups,
                ).fetchone()[0]
            else:
                pairs = 0
            active_sessions = db.execute(
                "SELECT COUNT(*) FROM web_sessions WHERE expires>?", (time.time(),)
            ).fetchone()[0]
            ai_doc = self._document(db, "ai:global:" + bot)
            if not ai_doc:
                ai_doc = self._document(db, "ai:global")
            disk = self._document(db, "system:disk")
            audit = [dict(row) for row in db.execute(
                "SELECT id,action,target,created FROM web_audit ORDER BY id DESC LIMIT 6"
            )]
            # Integrity checks belong to maintenance, not every page change.
            checked = db.execute("SELECT value FROM documents WHERE key='system:integrity'").fetchone()
            check = json.loads(checked[0]).get("ok", True) if checked else True
        db_path = self.store.path / "bot.sqlite3"
        database_bytes = sum(
            path.stat().st_size for path in (
                db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")
            ) if path.exists()
        )
        return {
            "health": "ok" if check else "warning",
            "bot": bot,
            "uptime_seconds": max(0, int(time.time() - STARTED_AT)),
            "database_bytes": database_bytes,
            "database_size": _size(database_bytes),
            "groups": len(groups),
            "roles": roles,
            "grants": grants,
            "feedback_open": feedback_open,
            "gallery_count": gallery_count,
            "gallery_bytes": gallery_bytes,
            "gallery_size": _size(gallery_bytes),
            "local_galleries": len(local_scopes),
            "learning_pairs": pairs,
            "ai_enabled": ai_doc.get("enabled", True) is not False,
            "active_sessions": active_sessions,
            "audit": audit,
            "disk_warning": bool(disk.get("warning")),
            "disk_free": _size(int(disk.get("free", 0))),
            "integrity_checked": bool(checked),
        }

    def groups(self, page: int, query: str = "", bot: str = "") -> dict:
        page, query = _page(page), _query(query)
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
            # Upgrades can open the admin UI before the first bot credential is
            # saved.  Keep legacy group metadata visible in that state so an
            # operator can inspect it instead of seeing an apparently empty DB.
            if bot:
                scopes_sql = self._groups_sql()
                params = [bot, bot]
            else:
                scopes_sql = """WITH scopes AS (
                    SELECT scope FROM bot_scopes WHERE kind='group'
                    UNION SELECT scope FROM ai_groups
                    UNION SELECT scope FROM group_metadata
                ) """
                params = []
            joined = scopes_sql + """
                SELECT s.scope,m.official_name,m.manual_name,m.checked,m.last_error,
                       CASE WHEN json_valid(d.value) THEN d.value ELSE '{}' END AS doc
                FROM scopes s LEFT JOIN group_metadata m ON m.scope=s.scope
                LEFT JOIN documents d ON d.key=s.scope
                WHERE length(s.scope)=64 AND s.scope NOT GLOB '*[^0-9a-f]*'
            """
            if query:
                joined += """ AND (instr(casefold(m.official_name),?)>0
                    OR instr(casefold(m.manual_name),?)>0 OR instr(substr(s.scope,1,10),?)>0
                    OR instr(casefold(json_extract(CASE WHEN json_valid(d.value) THEN d.value ELSE '{}' END,'$.server')),?)>0)"""
            params += [query]*4 if query else []
            total = db.execute("SELECT COUNT(*) FROM (" + joined + ")", params).fetchone()[0]
            rows = db.execute(joined + " ORDER BY s.scope LIMIT ? OFFSET ?",
                              (*params, PAGE_SIZE, (page-1)*PAGE_SIZE)).fetchall()
            selected = [row["scope"] for row in rows]
            result = []
            if selected:
                marks = ",".join("?" for _ in selected)
                learning_map = {r["scope"]: r for r in db.execute(
                    f"SELECT * FROM learning_config WHERE scope IN ({marks})", selected)}
                pair_counts = {r[0]: r[1] for r in db.execute(
                    f"SELECT scope,COUNT(*) FROM learning_pairs WHERE banned=0 AND scope IN ({marks}) GROUP BY scope", selected)}
                role_counts = {r[0]: r[1] for r in db.execute(
                    f"SELECT scope,COUNT(*) FROM ai_groups WHERE scope IN ({marks}) GROUP BY scope", selected)}
                gallery_stats = {r[0]: (r[1], r[2]) for r in db.execute(
                    f"SELECT scope,COUNT(*),COALESCE(SUM({SIZE_SQL}),0) FROM gallery WHERE scope IN ({marks}) GROUP BY scope", selected)}
                for row in rows:
                    scope = row["scope"]
                    doc = json.loads(row["doc"])
                    if not isinstance(doc, dict):
                        doc = {}
                    learned = learning_map.get(scope)
                    amount, size = gallery_stats.get(scope, (0, 0))
                    result.append({
                        "scope": scope, "tag": scope[:10],
                        "name": row["manual_name"] or row["official_name"] or "",
                        "manual_name": row["manual_name"] or "", "official_name": row["official_name"] or "",
                        "name_checked": row["checked"] or 0, "name_error": row["last_error"] or "",
                        "server": str(doc.get("server") or ""),
                        "quota": doc.get("quota", 100) if type(doc.get("quota", 100)) is int else 100,
                        "gallery_mode": "local" if doc.get("gallery_mode") == "local" else "public",
                        "disabled": sorted(v for v in doc.get("disabled", []) if isinstance(v,str) and v in MANAGED),
                        "custom_replies": len(doc.get("replies", {})), "hunt_rules": len(doc.get("hunt_rules", {})),
                        "roles": role_counts.get(scope, 0), "gallery_count": amount, "gallery_size": _size(size),
                        "learning": {
                            "enabled": bool(learned["enabled"]) if learned else False,
                            "answer_threshold": learned["answer_threshold"] if learned else 4,
                            "repeat_threshold": learned["repeat_threshold"] if learned else 3,
                            "repeat_enabled": bool(learned["repeat_enabled"]) if learned else True,
                            "repeat_probability": learned["repeat_probability"] if learned else 100,
                            "interrupt_repeat_enabled": bool(learned["interrupt_repeat_enabled"]) if learned else True,
                            "interrupt_repeat_probability": learned["interrupt_repeat_probability"] if learned else 25,
                            "image_enabled": bool(learned["image_enabled"]) if learned else True,
                            "library_mode": learned["library_mode"] if learned else "public",
                            "pairs": pair_counts.get(scope, 0),
                        },
                    })
        return {"items": result, "page": page, "total": total, "page_size": PAGE_SIZE,
                "query": query, "managed_commands": sorted(MANAGED)}

    def update_group(self, scope: str, data: dict, bot: str = "") -> None:
        if not SCOPE.fullmatch(scope):
            raise ToolError("群标识格式不正确。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            if scope not in self._known_groups(db, bot):
                raise ToolError("没有找到这个群。")
            doc = self._document(db, scope)
            if "display_name" in data:
                display_name = str(data["display_name"]).strip()
                if display_name:
                    display_name = clean(display_name, 40)
                db.execute("INSERT OR IGNORE INTO group_metadata(scope) VALUES(?)", (scope,))
                db.execute(
                    "UPDATE group_metadata SET manual_name=? WHERE scope=?",
                    (display_name, scope),
                )
            if "server" in data:
                server = str(data["server"]).strip()
                if server:
                    doc["server"] = clean(server, 30)
                else:
                    doc.pop("server", None)
            if "quota" in data:
                try:
                    quota = int(data["quota"])
                except (TypeError, ValueError):
                    raise ToolError("每日额度必须是数字。") from None
                if not 1 <= quota <= 10000:
                    raise ToolError("每日额度范围是 1～10000。")
                doc["quota"] = quota
            if "gallery_mode" in data:
                if data["gallery_mode"] not in {"public", "local"}:
                    raise ToolError("图库模式只能是公共图库或本群图库。")
                doc["gallery_mode"] = data["gallery_mode"]
            if "disabled" in data:
                if not isinstance(data["disabled"], list) or any(
                    not isinstance(value, str) or value not in MANAGED for value in data["disabled"]
                ):
                    raise ToolError("功能开关中包含不支持的命令。")
                doc["disabled"] = sorted(set(data["disabled"]))
            self._save_document(db, scope, doc)
            learning = data.get("learning")
            if learning is not None:
                if not isinstance(learning, dict):
                    raise ToolError("群聊学习设置格式不正确。")
                values = {
                    "enabled": int(bool(learning.get("enabled", False))),
                    "answer_threshold": int(learning.get("answer_threshold", 4)),
                    "repeat_threshold": int(learning.get("repeat_threshold", 3)),
                    "repeat_enabled": int(bool(learning.get("repeat_enabled", True))),
                    "repeat_probability": int(learning.get("repeat_probability", 100)),
                    "interrupt_repeat_enabled": int(bool(learning.get("interrupt_repeat_enabled", True))),
                    "interrupt_repeat_probability": int(learning.get("interrupt_repeat_probability", 25)),
                    "image_enabled": int(bool(learning.get("image_enabled", True))),
                    "library_mode": str(learning.get("library_mode", "public")),
                }
                if not 1 <= values["answer_threshold"] <= 10 or not 0 <= values["repeat_threshold"] <= 10:
                    raise ToolError("学习回复阈值应为 1～10，复读阈值应为 0～10。")
                if not 0 <= values["repeat_probability"] <= 100 or not 0 <= values["interrupt_repeat_probability"] <= 100:
                    raise ToolError("复读与打断复读概率应为 0～100%。")
                if values["library_mode"] not in {"public", "local"}:
                    raise ToolError("学习库模式只能是公开或本群私有。")
                db.execute(
                    "INSERT INTO learning_config(scope,enabled,answer_threshold,repeat_threshold,"
                    "repeat_enabled,repeat_probability,interrupt_repeat_enabled,interrupt_repeat_probability,"
                    "image_enabled,library_mode,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(scope) DO UPDATE SET "
                    "enabled=excluded.enabled,answer_threshold=excluded.answer_threshold,"
                    "repeat_threshold=excluded.repeat_threshold,repeat_enabled=excluded.repeat_enabled,"
                    "repeat_probability=excluded.repeat_probability,"
                    "interrupt_repeat_enabled=excluded.interrupt_repeat_enabled,"
                    "interrupt_repeat_probability=excluded.interrupt_repeat_probability,"
                    "image_enabled=excluded.image_enabled,"
                    "library_mode=excluded.library_mode,updated=excluded.updated",
                    (scope, values["enabled"], values["answer_threshold"], values["repeat_threshold"],
                     values["repeat_enabled"], values["repeat_probability"],
                     values["interrupt_repeat_enabled"], values["interrupt_repeat_probability"],
                     values["image_enabled"], values["library_mode"], time.time()),
                )
                if not values["enabled"]:
                    db.execute("DELETE FROM learning_last WHERE scope=?", (scope,))
                    db.execute("DELETE FROM learning_repeat_state WHERE scope=?", (scope,))
            self._audit(db, "更新群设置", f"{bot} · 群 {scope[:10]}")

    def set_group_plugin(self, scope: str, command: str, enabled: bool,
                         bot: str = "") -> None:
        """Toggle one whole plugin without overwriting other group form fields."""
        if not SCOPE.fullmatch(scope):
            raise ToolError("群标识格式不正确。")
        command = str(command or "").strip().lower()
        if command not in GROUP_PLUGIN_SWITCHES:
            raise ToolError("不支持这个插件总开关。")
        if type(enabled) is not bool:
            raise ToolError("插件开关状态格式不正确。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            if scope not in self._known_groups(db, bot):
                raise ToolError("没有找到这个群。")
            doc = self._document(db, scope)
            disabled = {
                value for value in doc.get("disabled", [])
                if isinstance(value, str) and value in MANAGED
            }
            if enabled:
                disabled.discard(command)
            else:
                disabled.add(command)
            doc["disabled"] = sorted(disabled)
            self._save_document(db, scope, doc)
            label = GROUP_PLUGIN_SWITCHES[command]
            self._audit(
                db, "开启群插件" if enabled else "关闭群插件",
                f"{bot} · 群 {scope[:10]} · {label}",
            )

    def roles(self, page: int, query: str = "", bot: str = "") -> dict:
        page, query = _page(page), _query(query)
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
            # Model defaults are normalized for search without exposing secrets.
            model_sql = "COALESCE(NULLIF(p.model,''),CASE WHEN p.provider='glm' THEN 'glm-4.7-flash' WHEN p.region='singapore' THEN 'qwen-flash-character' ELSE 'qwen-flash-character-2026-02-26' END)"
            joined = " FROM ai_profiles p LEFT JOIN ai_accounts a ON a.owner=p.owner"
            clause = " WHERE p.bot=?"
            params = [bot]
            if query:
                clause += f" AND instr(casefold(p.actor || char(10) || p.name || char(10) || p.provider || char(10) || p.region || char(10) || {model_sql}),?)>0"
                params.append(query)
            total = db.execute("SELECT COUNT(*)" + joined + clause, params).fetchone()[0]
            rows = db.execute(
                "SELECT p.actor,p.name,p.provider,p.region,p.model,(p.secret IS NOT NULL) AS configured,"
                "a.daily,(SELECT COUNT(*) FROM ai_groups g WHERE g.owner=p.actor AND g.bot=p.bot) AS groups" +
                joined + clause + " ORDER BY p.actor LIMIT ? OFFSET ?",
                (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))
            items = []
            for row in rows:
                try:
                    model = model_for(row["provider"], row["region"], row["model"]).code
                except ToolError:
                    model = "未知模型"
                items.append({
                    "id": row["actor"], "name": row["name"] or "未命名",
                    "provider": row["provider"], "region": row["region"], "model": model,
                    "configured": bool(row["configured"]), "daily": row["daily"] or 100,
                    "groups": row["groups"],
                })
            grant_sql = """ FROM ai_groups g JOIN ai_profiles p ON p.actor=g.owner
                LEFT JOIN group_metadata m ON m.scope=g.scope"""
            name = "COALESCE(NULLIF(m.manual_name,''),NULLIF(m.official_name,''),'')"
            grant_where = " WHERE g.bot=?"
            grant_params = [bot]
            if query:
                grant_where += f" AND instr(casefold(g.id || char(10) || p.name || char(10) || {name} || char(10) || substr(g.scope,1,10)),?)>0"
                grant_params.append(query)
            grant_total = db.execute("SELECT COUNT(*)" + grant_sql + grant_where, grant_params).fetchone()[0]
            grants = [dict(row) for row in db.execute(
                "SELECT g.id,substr(g.scope,1,10) AS group_tag,g.cap,g.paused,g.mention_only,"
                f"g.collect_min,g.collect_max,p.name,{name} AS group_name" +
                grant_sql + grant_where + " ORDER BY g.id LIMIT ? OFFSET ?",
                (*grant_params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        return {"items": items, "page": page, "total": total, "page_size": PAGE_SIZE,
                "query": query, "grants": grants, "grant_total": grant_total}

    def delete_role(self, identifier: str, bot: str = "") -> None:
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
        name = self.ai.delete_role_by_admin(identifier, bot)
        with self.store.connect() as db:
            self._audit(db, "删除 AI 角色", f"{bot} · {name} · {identifier}")

    def learning(self, page: int, query: str = "", scope: str = "",
                 status: str = "all", bot: str = "", order: str = "recent") -> dict:
        page = _page(page)
        query = _query(query, limit=80)
        if scope and not SCOPE.fullmatch(scope):
            raise ToolError("群标识格式不正确。")
        if status not in {"all", "active", "disabled"}:
            raise ToolError("学习内容状态不正确。")
        orders = {
            "recent": "p.updated DESC,p.id DESC",
            "count_desc": "p.count DESC,p.updated DESC,p.id DESC",
            "count_asc": "p.count ASC,p.updated DESC,p.id DESC",
        }
        if order not in orders:
            raise ToolError("学习内容排序方式不正确。")
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
            groups = [{"scope": r["scope"], "tag": r["scope"][:10], "name": r["name"]} for r in db.execute(
                self._groups_sql() + "SELECT s.scope,COALESCE(NULLIF(m.manual_name,''),m.official_name,'') AS name "
                "FROM scopes s LEFT JOIN group_metadata m ON m.scope=s.scope ORDER BY name,s.scope",
                (bot, bot))]

            where, params = ["p.scope IN (SELECT scope FROM bot_scopes WHERE bot=? AND kind='group')"], [bot]
            if scope:
                if scope not in {group["scope"] for group in groups}:
                    raise ToolError("这个群不属于当前机器人。")
                where.append("p.scope=?")
                params.append(scope)
            if status == "active":
                where.append("p.banned=0")
            elif status == "disabled":
                where.append("p.banned=1")
            group_name = "COALESCE(NULLIF(m.manual_name,''),NULLIF(m.official_name,''),'')"
            if query:
                where.append(
                    "(instr(lower(p.prompt_preview),?)>0 OR instr(lower(c.text),?)>0 OR "
                    f"instr(lower({group_name}),?)>0 OR instr(lower(substr(p.scope,1,10)),?)>0 OR "
                    "instr(CAST(p.id AS TEXT),?)>0)"
                )
                params.extend([query] * 5)
            clause = " WHERE " + " AND ".join(where) if where else ""
            joined = (
                " FROM learning_pairs p JOIN learning_contents c ON c.id=p.reply_content "
                "LEFT JOIN group_metadata m ON m.scope=p.scope"
            )
            total = db.execute("SELECT COUNT(*)" + joined + clause, params).fetchone()[0]
            rows = db.execute(
                "SELECT p.id,p.scope,p.prompt_preview,p.count,p.banned,p.created,p.updated,"
                f"c.kind,c.text,{group_name} AS group_name" + joined + clause +
                f" ORDER BY {orders[order]} LIMIT ? OFFSET ?",
                (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            )
            items = []
            for row in rows:
                items.append({
                    "id": row["id"], "scope": row["scope"], "group_tag": row["scope"][:10],
                    "group_name": row["group_name"], "prompt": row["prompt_preview"],
                    "reply": row["text"][:120] if row["kind"] == "text" else "[图片]",
                    "kind": row["kind"], "count": row["count"], "disabled": bool(row["banned"]),
                    "created": row["created"], "updated": row["updated"],
                })
        return {"items": items, "page": page, "total": total, "page_size": PAGE_SIZE,
                "query": query, "scope": scope, "status": status, "order": order,
                "groups": groups}

    def set_learning_pair(self, pair_id: int, disabled: bool, bot: str = "") -> None:
        if not 1 <= pair_id <= 2_147_483_647:
            raise ToolError("学习回复编号格式不正确。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            row = db.execute(
                "SELECT p.scope,c.digest,c.kind,c.text FROM learning_pairs p "
                "JOIN learning_contents c ON c.id=p.reply_content "
                "JOIN bot_scopes s ON s.scope=p.scope WHERE p.id=? AND s.bot=?",
                (pair_id, bot),
            ).fetchone()
            if not row:
                raise ToolError("没有找到这个学习回复。")
            preview = row["text"][:60] if row["kind"] == "text" else "[图片]"
            if disabled:
                db.execute(
                    "INSERT OR IGNORE INTO learning_bans(scope,digest,preview,created) VALUES(?,?,?,?)",
                    (row["scope"], row["digest"], preview, time.time()),
                )
            else:
                db.execute(
                    "DELETE FROM learning_bans WHERE scope=? AND digest=?",
                    (row["scope"], row["digest"]),
                )
            db.execute(
                "UPDATE learning_pairs SET banned=? WHERE scope=? AND reply_content IN "
                "(SELECT id FROM learning_contents WHERE scope=? AND digest=?)",
                (int(disabled), row["scope"], row["scope"], row["digest"]),
            )
            self._audit(db, "禁用学习回复" if disabled else "恢复学习回复", f"回复 #{pair_id}")

    def grant_action(self, identifier: str, action: str, bot: str = "") -> None:
        if not re.fullmatch(r"[a-f0-9]{12}", identifier):
            raise ToolError("授权编号格式不正确。")
        if action not in {"pause", "resume", "clear", "remove"}:
            raise ToolError("不支持的群授权操作。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            row = db.execute(
                "SELECT scope,owner FROM ai_groups WHERE id=? AND bot=?", (identifier, bot)
            ).fetchone()
            if not row:
                raise ToolError("没有找到这个群授权。")
            scope = conversation(row["scope"], row["owner"])
            db.execute("DELETE FROM ai_history WHERE scope=?", (scope,))
            db.execute(
                "INSERT OR REPLACE INTO ai_sessions VALUES(?,?)", (scope, secrets.token_hex(16))
            )
            db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
            db.execute("DELETE FROM ai_group_seen WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
            if action == "remove":
                db.execute("DELETE FROM ai_groups WHERE id=?", (identifier,))
                db.execute("DELETE FROM ai_proposals WHERE scope=?", (row["scope"],))
            elif action in {"pause", "resume"}:
                db.execute("UPDATE ai_groups SET paused=? WHERE id=?", (int(action == "pause"), identifier))
            self._audit(db, {"pause": "暂停角色", "resume": "恢复角色", "clear": "清空角色记忆",
                             "remove": "移除群角色"}[action], "授权 " + identifier)

    def set_ai_enabled(self, enabled: bool, bot: str = "") -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            doc = self._document(db, "ai:global:" + bot)
            doc["enabled"] = bool(enabled)
            self._save_document(db, "ai:global:" + bot, doc)
            if not enabled:
                db.execute(
                    "DELETE FROM ai_group_buffer WHERE owner IN "
                    "(SELECT actor FROM ai_profiles WHERE bot=?)", (bot,)
                )
                db.execute(
                    "DELETE FROM ai_group_seen WHERE owner IN "
                    "(SELECT actor FROM ai_profiles WHERE bot=?)", (bot,)
                )
            self._audit(db, "全局开启 AI" if enabled else "全局暂停 AI", bot + " · 全部角色")

    def gallery(self, page: int, mode: str = "all", category: str = "",
                group_scope: str = "") -> dict:
        page = _page(page)
        if mode not in {"all", "public", "local"}:
            raise ToolError("图库范围不正确。")
        category = category.strip()
        if len(category) > 20:
            raise ToolError("分类名称过长。")
        group_scope = group_scope.strip()
        if group_scope and not SCOPE.fullmatch(group_scope):
            raise ToolError("群标识格式不正确。")
        with self.store.connect() as db:
            # Gallery rows whose scope is a known QQ group are always private
            # group data.  This classification must not depend on the group's
            # *current* gallery mode: switching back to public intentionally
            # keeps the old private images in their original group library.
            group_scopes = set(self._known_groups(db))
            stored_scopes = {row[0] for row in db.execute("SELECT DISTINCT scope FROM gallery")}
            group_scopes.update(row[0] for row in db.execute("SELECT DISTINCT scope FROM gallery WHERE visibility='local'"))
            local_scopes = sorted(group_scopes & stored_scopes)
            if group_scope and group_scope not in group_scopes:
                raise ToolError("没有找到这个群。")

            scope_where, scope_params = [], []
            if mode == "local":
                if group_scope:
                    scope_where.append("scope=?")
                    scope_params.append(group_scope)
                elif local_scopes:
                    placeholders = ",".join("?" for _ in local_scopes)
                    scope_where.append(f"scope IN ({placeholders})")
                    scope_params.extend(local_scopes)
                else:
                    scope_where.append("0")
                scope_where.append("visibility<>'public'")
            elif mode == "public" and group_scopes:
                placeholders = ",".join("?" for _ in group_scopes)
                scope_where.append(f"(visibility='public' OR (visibility='' AND scope NOT IN ({placeholders})))")
                scope_params.extend(sorted(group_scopes))
            elif mode == "public":
                scope_where.append("visibility<>'local'")

            where, params = list(scope_where), list(scope_params)
            if category:
                where.append("category=?")
                params.append(category)
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = db.execute("SELECT COUNT(*) FROM gallery" + clause, params).fetchone()[0]
            rows = db.execute(
                f"SELECT id,scope,category,visibility,{SIZE_SQL} AS bytes FROM gallery" + clause +
                " ORDER BY id DESC LIMIT ? OFFSET ?", (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)
            ).fetchall()
            names = {
                row["scope"]: row["name"] for row in db.execute(
                    "SELECT scope,COALESCE(NULLIF(manual_name,''),NULLIF(official_name,''),'') AS name "
                    "FROM group_metadata"
                )
            }
            local_groups = [{
                "scope": scope, "tag": scope[:10], "name": names.get(scope, ""),
            } for scope in local_scopes]
            local_groups.sort(key=lambda item: (item["name"] or item["tag"]).casefold())
            items = [{
                "id": row["id"], "category": row["category"], "bytes": row["bytes"],
                "size": _size(row["bytes"]), "mode": row["visibility"] or ("local" if row["scope"] in group_scopes else "public"),
                "group_tag": row["scope"][:10] if row["scope"] in group_scopes else "公共",
                "group_name": names.get(row["scope"], "") if row["scope"] in group_scopes else "",
            } for row in rows]
            category_clause = " WHERE " + " AND ".join(scope_where) if scope_where else ""
            categories = [row[0] for row in db.execute(
                "SELECT DISTINCT category FROM gallery" + category_clause +
                " ORDER BY category LIMIT 200", scope_params,
            )]
        return {"items": items, "page": page, "total": total, "page_size": PAGE_SIZE,
                "categories": categories, "groups": local_groups,
                "mode": mode, "group_scope": group_scope}

    def gallery_image(self, item_id: int) -> tuple[bytes, str] | None:
        with self.store.connect() as db:
            row = db.execute("SELECT image,object_key FROM gallery WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        image = read_row(self.store, row)
        if image.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif image.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif image.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        else:
            return None
        return image, mime

    def gallery_thumbnail(self, item_id: int) -> bytes | None:
        with self.store.connect() as db:
            row = db.execute("SELECT object_key FROM gallery WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        result = self.gallery_image(item_id)
        if not result:
            return None
        return configured_objects(self.store.path, LocalObjects).thumbnail(result[0])

    def delete_gallery(self, item_id: int) -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT category FROM gallery WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise ToolError("没有找到这张图片。")
            db.execute("DELETE FROM gallery WHERE id=?", (item_id,))
            self._audit(db, "删除图片", f"#{item_id} · {row['category']}")

    def feedback(self, page: int, closed: bool, bot: str = "") -> dict:
        page = _page(page)
        with self.store.connect() as db:
            bot = self._select_bot(db, bot)
            total = db.execute(
                "SELECT COUNT(*) FROM feedback WHERE closed=? AND bot=?",
                (int(closed), bot),
            ).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT id,body,created,closed FROM feedback WHERE closed=? AND bot=? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(closed), bot, PAGE_SIZE, (page - 1) * PAGE_SIZE)
            )]
        return {"items": rows, "page": page, "total": total, "page_size": PAGE_SIZE}

    def set_feedback(self, item_id: int, closed: bool, bot: str = "") -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bot = self._select_bot(db, bot)
            if not db.execute(
                "UPDATE feedback SET closed=? WHERE id=? AND bot=?",
                (int(closed), item_id, bot),
            ).rowcount:
                raise ToolError("没有找到这条反馈。")
            self._audit(
                db, "完成反馈" if closed else "重开反馈",
                f"{bot} · 反馈 #{item_id}",
            )

    def audit(self, page: int) -> dict:
        page = _page(page)
        with self.store.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM web_audit").fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT id,action,target,created FROM web_audit ORDER BY id DESC LIMIT ? OFFSET ?",
                (PAGE_SIZE, (page - 1) * PAGE_SIZE),
            )]
        return {"items": rows, "page": page, "total": total, "page_size": PAGE_SIZE}


def _secure(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


async def _body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "只接受 JSON 请求。")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 16 * 1024:
            raise HTTPException(413, "请求内容过大。")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "请求格式不正确。") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "请求格式不正确。")
    return value


def install_web_admin(app: FastAPI, store: Store | None = None,
                      bot_runtime: Any = None) -> WebAdmin:
    admin = WebAdmin(store or Store(), bot_runtime)

    def require(request: Request, *, csrf: bool = False) -> dict:
        session = admin.session(request.cookies.get(COOKIE))
        if not session:
            raise HTTPException(401, "请先登录。")
        if csrf:
            supplied = request.headers.get("x-admin-csrf", "")
            if not secrets.compare_digest(supplied, session["csrf"]):
                raise HTTPException(403, "页面凭据已失效，请刷新后重试。")
            origin = request.headers.get("origin")
            if origin:
                try:
                    if urlsplit(origin).netloc != request.headers.get("host", ""):
                        raise HTTPException(403, "拒绝跨站请求。")
                except ValueError:
                    raise HTTPException(403, "拒绝跨站请求。") from None
        return session

    def ok(data: object = None) -> JSONResponse:
        return _secure(JSONResponse({"ok": True, "data": data}))

    def safe(call):
        try:
            return call()
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from None

    async def safe_async(call):
        return await run_in_threadpool(safe, call)

    async def reconcile_bot(app_id: str) -> None:
        if admin.bot_runtime is None:
            return
        try:
            await admin.bot_runtime.reconcile(app_id)
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            # Credential values must never enter exception logs or responses.
            print(
                f"QQ bot live reload failed ({type(exc).__name__})",
                file=sys.stderr,
            )
            raise HTTPException(
                500, "设置已经加密保存，但即时连接失败；请检查后重新保存。",
            ) from None

    async def require_async(request: Request, *, csrf: bool = False):
        return await run_in_threadpool(require, request, csrf=csrf)

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    async def admin_page() -> Response:
        return _secure(FileResponse(WEB_ROOT / "index.html", media_type="text/html; charset=utf-8"))

    @app.get("/admin/assets/{filename}", include_in_schema=False)
    async def admin_asset(filename: str) -> Response:
        if filename not in {"admin.css", "admin.js"}:
            raise HTTPException(404)
        media = "text/css; charset=utf-8" if filename.endswith(".css") else "text/javascript; charset=utf-8"
        return _secure(FileResponse(WEB_ROOT / filename, media_type=media))

    @app.post("/admin/api/login", include_in_schema=False)
    async def login(request: Request) -> Response:
        data = await _body(request)
        client = request.client.host if request.client else "unknown"
        session, csrf = await safe_async(lambda: admin.authenticate(
            data.get("username", ""), data.get("password", ""), client,
        ))
        response = ok({"csrf": csrf, "expires_in": SESSION_TTL})
        response.set_cookie(
            COOKIE, session, max_age=SESSION_TTL, path="/admin", httponly=True,
            samesite="strict", secure=os.environ.get("WEB_ADMIN_SECURE_COOKIE", "false").lower() in
            {"1", "true", "yes", "on"},
        )
        return response

    @app.get("/admin/api/session", include_in_schema=False)
    def session(request: Request) -> Response:
        current = require(request)
        return ok({"csrf": current["csrf"], "expires": current["expires"]})

    @app.post("/admin/api/logout", include_in_schema=False)
    def logout(request: Request) -> Response:
        require(request, csrf=True)
        admin.logout(request.cookies.get(COOKIE))
        response = ok()
        response.delete_cookie(COOKIE, path="/admin")
        return response

    @app.get("/admin/api/bots", include_in_schema=False)
    def bots(request: Request) -> Response:
        require(request)
        return ok(admin.bots())

    @app.post("/admin/api/bots", include_in_schema=False)
    async def save_bot(request: Request) -> Response:
        await require_async(request, csrf=True)
        data = await _body(request)
        result = await safe_async(lambda: admin.save_bot(data))
        await reconcile_bot(result["app_id"])
        listing = await safe_async(admin.bots)
        current = next(
            item for item in listing["items"] if item["app_id"] == result["app_id"]
        )
        return ok({**result, "state": current["state"]})

    @app.post("/admin/api/bots/{app_id}/delete", include_in_schema=False)
    async def delete_bot(app_id: str, request: Request) -> Response:
        await require_async(request, csrf=True)
        await _body(request)
        result = await safe_async(lambda: admin.delete_bot(app_id))
        await reconcile_bot(app_id)
        return ok(result)

    @app.get("/admin/api/overview", include_in_schema=False)
    def overview(request: Request, bot: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.overview(bot)))

    @app.post("/admin/api/ai-global", include_in_schema=False)
    async def ai_global(request: Request, bot: str = "") -> Response:
        await require_async(request, csrf=True)
        data = await _body(request)
        if type(data.get("enabled")) is not bool:
            raise HTTPException(400, "enabled 必须是布尔值。")
        await safe_async(lambda: admin.set_ai_enabled(data["enabled"], bot))
        return ok()

    @app.get("/admin/api/groups", include_in_schema=False)
    def groups(request: Request, page: int = 1, q: str = "", bot: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.groups(page, q, bot)))

    @app.post("/admin/api/groups/{scope}/plugin", include_in_schema=False)
    async def set_group_plugin(scope: str, request: Request, bot: str = "") -> Response:
        await require_async(request, csrf=True)
        data = await _body(request)
        if type(data.get("enabled")) is not bool:
            raise HTTPException(400, "enabled 必须是布尔值。")
        await safe_async(lambda: admin.set_group_plugin(
            scope, data.get("command", ""), data["enabled"], bot,
        ))
        return ok()

    @app.post("/admin/api/groups/{scope}", include_in_schema=False)
    async def update_group(scope: str, request: Request, bot: str = "") -> Response:
        await require_async(request, csrf=True)
        data = await _body(request)
        await safe_async(lambda: admin.update_group(scope, data, bot))
        return ok()

    @app.get("/admin/api/roles", include_in_schema=False)
    def roles(request: Request, page: int = 1, q: str = "", bot: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.roles(page, q, bot)))

    @app.post("/admin/api/roles/{identifier}/delete", include_in_schema=False)
    async def delete_role(identifier: str, request: Request, bot: str = "") -> Response:
        await require_async(request, csrf=True)
        await _body(request)
        await safe_async(lambda: admin.delete_role(identifier, bot))
        return ok()

    @app.get("/admin/api/learning", include_in_schema=False)
    def learning(request: Request, page: int = 1, q: str = "", scope: str = "",
                       status: str = "all", order: str = "recent", bot: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.learning(page, q, scope, status, bot, order)))

    @app.post("/admin/api/learning/{pair_id}/{action}", include_in_schema=False)
    async def learning_action(pair_id: int, action: str, request: Request,
                              bot: str = "") -> Response:
        await require_async(request, csrf=True)
        await _body(request)
        if action not in {"disable", "restore"}:
            raise HTTPException(400, "不支持的学习内容操作。")
        await safe_async(lambda: admin.set_learning_pair(pair_id, action == "disable", bot))
        return ok()

    @app.post("/admin/api/grants/{identifier}/{action}", include_in_schema=False)
    async def grant_action(identifier: str, action: str, request: Request,
                           bot: str = "") -> Response:
        await require_async(request, csrf=True)
        await _body(request)
        await safe_async(lambda: admin.grant_action(identifier, action, bot))
        return ok()

    @app.get("/admin/api/gallery", include_in_schema=False)
    def gallery(request: Request, page: int = 1, mode: str = "all", category: str = "",
                      scope: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.gallery(page, mode, category, scope)))

    @app.get("/admin/api/gallery/{item_id}/image", include_in_schema=False)
    def gallery_image(item_id: int, request: Request) -> Response:
        require(request)
        result = admin.gallery_image(item_id)
        if result is None:
            raise HTTPException(404, "图片不存在或格式不受支持。")
        return _secure(Response(result[0], media_type=result[1]))

    @app.post("/admin/api/gallery/{item_id}/delete", include_in_schema=False)
    async def delete_gallery(item_id: int, request: Request) -> Response:
        await require_async(request, csrf=True)
        await _body(request)
        await safe_async(lambda: admin.delete_gallery(item_id))
        return ok()

    @app.get("/admin/api/gallery/{item_id}/thumbnail", include_in_schema=False)
    def gallery_thumbnail(item_id: int, request: Request) -> Response:
        require(request)
        result = safe(lambda: admin.gallery_thumbnail(item_id))
        if result is None:
            raise HTTPException(404, "图片不存在。")
        return _secure(Response(result, media_type="image/jpeg"))

    @app.get("/admin/api/feedback", include_in_schema=False)
    def feedback(request: Request, page: int = 1, closed: bool = False,
                 bot: str = "") -> Response:
        require(request)
        return ok(safe(lambda: admin.feedback(page, closed, bot)))

    @app.post("/admin/api/feedback/{item_id}", include_in_schema=False)
    async def feedback_status(item_id: int, request: Request, bot: str = "") -> Response:
        await require_async(request, csrf=True)
        data = await _body(request)
        if type(data.get("closed")) is not bool:
            raise HTTPException(400, "closed 必须是布尔值。")
        await safe_async(lambda: admin.set_feedback(item_id, data["closed"], bot))
        return ok()

    @app.get("/admin/api/audit", include_in_schema=False)
    def audit(request: Request, page: int = 1) -> Response:
        require(request)
        return ok(safe(lambda: admin.audit(page)))

    return admin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="机器人网页后台账号管理 / Web admin account")
    parser.add_argument("action", choices=("account", "status", "revoke-all"))
    args = parser.parse_args(argv)
    admin = WebAdmin(Store())
    if args.action == "account":
        try:
            username = sys.stdin.buffer.readline(512).decode("utf-8").rstrip("\r\n")
            password = sys.stdin.buffer.readline(1024).decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            print("用户名或密码必须是 UTF-8 文本。", file=sys.stderr)
            return 2
        if not username or not password:
            print("请通过 bot.cmd 或 bot.sh 的交互式命令设置账号。", file=sys.stderr)
            return 2
        try:
            result = admin.set_account(username, password)
        except ToolError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        action = "已创建" if result["created"] else "已更新"
        print(f"网页后台账号{action}：{result['username']}")
        print(f"已注销旧网页会话：{result['revoked']} 个。现在可打开 http://127.0.0.1:8080/admin 登录。")
    elif args.action == "status":
        status = admin.auth_status()
        account = status["username"] if status["configured"] else "未设置"
        print(f"网页账号：{account}；有效网页会话：{status['sessions']}")
    else:
        count = admin.revoke_sessions()
        print(f"已撤销 {count} 个网页会话；后台账号和密码没有改变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
