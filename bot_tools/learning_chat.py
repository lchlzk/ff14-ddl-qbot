"""Native QQ-adapter port of the learning-chat core.

SPDX-License-Identifier: AGPL-3.0-only

This module adapts the learning model from nonebot-plugin-learning-chat 0.4.0
by CMHopeSunshine ("惜月") for nonebot-adapter-qq.  The persistence,
permissions, input validation and message representation are rewritten for
this bot; the original project is https://github.com/CMHopeSunshine/nonebot-plugin-learning-chat.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
import unicodedata
from dataclasses import dataclass

from .storage import Identity, Store, ToolError


MAX_TEXT_CHARACTERS = 300
MAX_TEXT_BYTES = 1200
MAX_PAIRS_PER_GROUP = 5000
MAX_TEXT_CONTENTS_PER_GROUP = 6000
MAX_IMAGE_CONTENTS_PER_GROUP = 100
MAX_IMAGE_BYTES_PER_GROUP = 100 * 1024 * 1024
MAX_SINGLE_IMAGE_BYTES = 4 * 1024 * 1024
MAX_BLOCK_WORDS = 50
SEEN_TTL = 24 * 60 * 60
CONTEXT_TTL = 60 * 60
LEARN_MAX_COUNT = 6
PAGE_SIZE = 5

SECRET_LIKE = re.compile(r"(?i)(?:sk-|api[_-]?key|token|secret)[=: ]*[A-Za-z0-9._-]{16,}")
LONG_CREDENTIAL = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9])")
URL_LIKE = re.compile(r"(?i)(?:https?://|www\.)")


def group_only(who: Identity) -> None:
    if who.private or not who.scope.startswith("group:"):
        raise ToolError("群聊学习只能在 QQ 群中使用。")


def _clip(value: str, characters: int, byte_limit: int) -> str:
    result = " ".join(str(value).split())[:characters]
    while result and len(result.encode("utf-8")) > byte_limit:
        result = result[:-1]
    return result


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    # Ignore ordinary punctuation differences but retain letters, numbers,
    # CJK text and underscores.  This is deterministic and dependency-free.
    value = re.sub(r"[^0-9a-z_\u3400-\u9fff]+", "", value)
    return value[:240]


@dataclass(frozen=True)
class LearningContent:
    kind: str
    digest: str
    key: str
    preview: str
    text: str = ""
    image: bytes | None = None

    @classmethod
    def from_text(cls, raw: str) -> "LearningContent | None":
        text = _clip(raw, MAX_TEXT_CHARACTERS, MAX_TEXT_BYTES)
        if len(text) < 2 or text.startswith("/"):
            return None
        if URL_LIKE.search(text) or SECRET_LIKE.search(text) or LONG_CREDENTIAL.search(text):
            return None
        normalized = _normalize(text)
        if len(normalized) < 2:
            return None
        digest = hashlib.sha256(("text\0" + text).encode("utf-8")).hexdigest()
        key = "text:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return cls("text", digest, key, text[:60], text=text)

    @classmethod
    def from_image(cls, image: bytes) -> "LearningContent | None":
        if not image or len(image) > MAX_SINGLE_IMAGE_BYTES:
            return None
        digest = hashlib.sha256(b"image\0" + image).hexdigest()
        return cls("image", digest, "image:" + digest, "[图片]", image=image)


@dataclass(frozen=True)
class LearningReply:
    pair_id: int | None
    kind: str
    text: str = ""
    image: bytes | None = None
    reason: str = "learned"


class LearningStore:
    """Bounded learning storage with public or group-private reply pools."""

    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS learning_config (
                    scope TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    answer_threshold INTEGER NOT NULL DEFAULT 4,
                    repeat_threshold INTEGER NOT NULL DEFAULT 3,
                    repeat_enabled INTEGER NOT NULL DEFAULT 1,
                    repeat_probability INTEGER NOT NULL DEFAULT 100,
                    interrupt_repeat_enabled INTEGER NOT NULL DEFAULT 1,
                    interrupt_repeat_probability INTEGER NOT NULL DEFAULT 25,
                    image_enabled INTEGER NOT NULL DEFAULT 1,
                    library_mode TEXT NOT NULL DEFAULT 'public',
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS learning_contents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL, digest TEXT NOT NULL,
                    kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', image BLOB,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    UNIQUE(scope,digest));
                CREATE INDEX IF NOT EXISTS learning_contents_scope
                    ON learning_contents(scope,id);
                CREATE TABLE IF NOT EXISTS learning_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL, prompt_key TEXT NOT NULL,
                    prompt_preview TEXT NOT NULL, reply_content INTEGER NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    banned INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    UNIQUE(scope,prompt_key,reply_content));
                CREATE INDEX IF NOT EXISTS learning_pairs_lookup
                    ON learning_pairs(scope,prompt_key,banned,count);
                CREATE INDEX IF NOT EXISTS learning_pairs_public_lookup
                    ON learning_pairs(prompt_key,banned,count DESC,updated DESC);
                CREATE INDEX IF NOT EXISTS learning_pairs_updated ON learning_pairs(updated DESC,id DESC);
                CREATE INDEX IF NOT EXISTS learning_pairs_scope_updated ON learning_pairs(scope,updated DESC,id DESC);
                CREATE INDEX IF NOT EXISTS learning_contents_kind ON learning_contents(scope,kind);
                CREATE TABLE IF NOT EXISTS learning_last (
                    scope TEXT PRIMARY KEY, actor TEXT NOT NULL,
                    content INTEGER NOT NULL, prompt_key TEXT NOT NULL,
                    message TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS learning_repeat_state (
                    scope TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    actors TEXT NOT NULL, count INTEGER NOT NULL,
                    replied INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS learning_seen (
                    scope TEXT NOT NULL, message TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(scope,message));
                CREATE INDEX IF NOT EXISTS learning_seen_created ON learning_seen(created);
                CREATE INDEX IF NOT EXISTS learning_repeat_updated ON learning_repeat_state(updated);
                CREATE TABLE IF NOT EXISTS learning_bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL, digest TEXT NOT NULL,
                    preview TEXT NOT NULL, created REAL NOT NULL,
                    UNIQUE(scope,digest));
                CREATE TABLE IF NOT EXISTS learning_block_words (
                    scope TEXT NOT NULL, word TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(scope,word));
                CREATE TABLE IF NOT EXISTS learning_block_users (
                    scope TEXT NOT NULL, actor TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(scope,actor));
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(learning_config)")}
            if "library_mode" not in columns:
                db.execute(
                    "ALTER TABLE learning_config ADD COLUMN library_mode TEXT NOT NULL DEFAULT 'public'"
                )
            migrations = {
                "repeat_enabled": "INTEGER NOT NULL DEFAULT 1",
                "repeat_probability": "INTEGER NOT NULL DEFAULT 100",
                "interrupt_repeat_enabled": "INTEGER NOT NULL DEFAULT 1",
                "interrupt_repeat_probability": "INTEGER NOT NULL DEFAULT 25",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE learning_config ADD COLUMN {column} {definition}")

    @staticmethod
    def _default() -> dict:
        return {
            "enabled": 0,
            "answer_threshold": 4,
            "repeat_threshold": 3,
            "repeat_enabled": 1,
            "repeat_probability": 100,
            "interrupt_repeat_enabled": 1,
            "interrupt_repeat_probability": 25,
            "image_enabled": 1,
            "library_mode": "public",
        }

    def _config(self, db, scope: str) -> dict:
        row = db.execute("SELECT * FROM learning_config WHERE scope=?", (scope,)).fetchone()
        return dict(row) if row else self._default()

    def enabled(self, who: Identity) -> bool:
        group_only(who)
        with self.store.connect() as db:
            row = db.execute("SELECT enabled FROM learning_config WHERE scope=?", (who.scope_key,)).fetchone()
            return bool(row and row[0])

    def config(self, who: Identity) -> dict:
        """Hot-path settings only; do not count the library for every message."""
        group_only(who)
        with self.store.connect() as db:
            return self._config(db, who.scope_key)

    def settings(self, who: Identity) -> dict:
        group_only(who)
        with self.store.connect() as db:
            result = self._config(db, who.scope_key)
            result.update({
                "pairs": db.execute(
                    "SELECT COUNT(*) FROM learning_pairs WHERE scope=? AND banned=0",
                    (who.scope_key,),
                ).fetchone()[0],
                "contents": db.execute(
                    "SELECT COUNT(*) FROM learning_contents WHERE scope=?",
                    (who.scope_key,),
                ).fetchone()[0],
                "images": db.execute(
                    "SELECT COUNT(*) FROM learning_contents WHERE scope=? AND kind='image'",
                    (who.scope_key,),
                ).fetchone()[0],
                "image_bytes": db.execute(
                    "SELECT COALESCE(SUM(length(image)),0) FROM learning_contents WHERE scope=? AND kind='image'",
                    (who.scope_key,),
                ).fetchone()[0],
                "bans": db.execute(
                    "SELECT COUNT(*) FROM learning_bans WHERE scope=?", (who.scope_key,)
                ).fetchone()[0],
                "blocked_words": db.execute(
                    "SELECT COUNT(*) FROM learning_block_words WHERE scope=?", (who.scope_key,)
                ).fetchone()[0],
                "blocked_users": db.execute(
                    "SELECT COUNT(*) FROM learning_block_users WHERE scope=?", (who.scope_key,)
                ).fetchone()[0],
            })
            return result

    def update(self, who: Identity, field: str, value: int | str) -> dict:
        group_only(who)
        if field == "library_mode":
            if not (who.group_role == "owner" or self.store.is_admin(who, owner=True)):
                raise ToolError("公开/私有学习库只能由本群群主或机器人总管理员切换。")
        else:
            self.store.require_admin(who)
        ranges = {
            "enabled": (0, 1),
            "answer_threshold": (1, 10),
            "repeat_threshold": (0, 10),
            "repeat_enabled": (0, 1),
            "repeat_probability": (0, 100),
            "interrupt_repeat_enabled": (0, 1),
            "interrupt_repeat_probability": (0, 100),
            "image_enabled": (0, 1),
        }
        if field == "library_mode":
            if value not in {"public", "local"}:
                raise ToolError("学习库模式只能是公开或本群私有。")
        elif field not in ranges or not isinstance(value, int) or not ranges[field][0] <= value <= ranges[field][1]:
            raise ToolError("群聊学习设置值无效。")
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO bot_scopes(scope,bot,kind,updated) VALUES(?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET bot=excluded.bot,kind='group',updated=excluded.updated",
                (who.scope_key, who.bot, "group", now),
            )
            db.execute(
                "INSERT OR IGNORE INTO learning_config(scope,updated) VALUES(?,?)",
                (who.scope_key, now),
            )
            db.execute(f"UPDATE learning_config SET {field}=?,updated=? WHERE scope=?", (value, now, who.scope_key))
            if field == "enabled" and not value:
                db.execute("DELETE FROM learning_last WHERE scope=?", (who.scope_key,))
                db.execute("DELETE FROM learning_repeat_state WHERE scope=?", (who.scope_key,))
            return self._config(db, who.scope_key)

    def block_word(self, who: Identity, word: str) -> None:
        group_only(who)
        self.store.require_admin(who)
        word = _clip(word, 20, 60).casefold()
        if len(word) < 1 or word.startswith("/"):
            raise ToolError("屏蔽词应为 1～20 个普通文字。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT 1 FROM learning_block_words WHERE scope=? AND word=?",
                (who.scope_key, word),
            ).fetchone()
            if not exists and db.execute(
                "SELECT COUNT(*) FROM learning_block_words WHERE scope=?", (who.scope_key,)
            ).fetchone()[0] >= MAX_BLOCK_WORDS:
                raise ToolError(f"本群最多设置 {MAX_BLOCK_WORDS} 个学习屏蔽词。")
            db.execute(
                "INSERT OR IGNORE INTO learning_block_words VALUES(?,?,?)",
                (who.scope_key, word, time.time()),
            )

    def unblock_word(self, who: Identity, word: str) -> None:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            if not db.execute(
                "DELETE FROM learning_block_words WHERE scope=? AND word=?",
                (who.scope_key, word.casefold()),
            ).rowcount:
                raise ToolError("本群没有这个学习屏蔽词。")

    def words(self, who: Identity) -> list[str]:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            return [row[0] for row in db.execute(
                "SELECT word FROM learning_block_words WHERE scope=? ORDER BY created DESC",
                (who.scope_key,),
            )]

    def block_user(self, who: Identity, actor: str) -> None:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO learning_block_users VALUES(?,?,?)",
                (who.scope_key, actor, time.time()),
            )

    def unblock_user(self, who: Identity, actor: str) -> None:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            if not db.execute(
                "DELETE FROM learning_block_users WHERE scope=? AND actor=?",
                (who.scope_key, actor),
            ).rowcount:
                raise ToolError("该成员当前不在学习屏蔽名单中。")

    def pairs(self, who: Identity, page: int) -> tuple[list[dict], int]:
        group_only(who)
        self.store.require_admin(who)
        if not 1 <= page <= 1000:
            raise ToolError("页码应为 1～1000。")
        with self.store.connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM learning_pairs WHERE scope=? AND banned=0", (who.scope_key,)
            ).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT p.id,p.prompt_preview,p.count,c.kind,c.text "
                "FROM learning_pairs p JOIN learning_contents c ON c.id=p.reply_content "
                "WHERE p.scope=? AND p.banned=0 ORDER BY p.updated DESC,p.id DESC LIMIT ? OFFSET ?",
                (who.scope_key, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            )]
            for row in rows:
                row["reply_preview"] = row["text"][:60] if row["kind"] == "text" else "[图片]"
            return rows, total

    def ban_pair(self, who: Identity, pair_id: int) -> dict:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT c.digest,c.kind,c.text FROM learning_pairs p "
                "JOIN learning_contents c ON c.id=p.reply_content "
                "WHERE p.scope=? AND p.id=?",
                (who.scope_key, pair_id),
            ).fetchone()
            if not row:
                raise ToolError("没有这个本群学习回复编号。")
            preview = row["text"][:60] if row["kind"] == "text" else "[图片]"
            db.execute(
                "INSERT OR IGNORE INTO learning_bans(scope,digest,preview,created) VALUES(?,?,?,?)",
                (who.scope_key, row["digest"], preview, time.time()),
            )
            db.execute(
                "UPDATE learning_pairs SET banned=1 WHERE scope=? AND reply_content IN "
                "(SELECT id FROM learning_contents WHERE scope=? AND digest=?)",
                (who.scope_key, who.scope_key, row["digest"]),
            )
            ban = db.execute(
                "SELECT id,preview FROM learning_bans WHERE scope=? AND digest=?",
                (who.scope_key, row["digest"]),
            ).fetchone()
            return dict(ban)

    def ban_content(self, who: Identity, content: LearningContent) -> dict:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO learning_bans(scope,digest,preview,created) VALUES(?,?,?,?)",
                (who.scope_key, content.digest, content.preview, time.time()),
            )
            db.execute(
                "UPDATE learning_pairs SET banned=1 WHERE scope=? AND reply_content IN "
                "(SELECT id FROM learning_contents WHERE scope=? AND digest=?)",
                (who.scope_key, who.scope_key, content.digest),
            )
            row = db.execute(
                "SELECT id,preview FROM learning_bans WHERE scope=? AND digest=?",
                (who.scope_key, content.digest),
            ).fetchone()
            return dict(row)

    def bans(self, who: Identity, page: int) -> tuple[list[dict], int]:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM learning_bans WHERE scope=?", (who.scope_key,)
            ).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT id,preview FROM learning_bans WHERE scope=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (who.scope_key, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            )]
            return rows, total

    def unban(self, who: Identity, ban_id: int) -> None:
        group_only(who)
        self.store.require_admin(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT digest FROM learning_bans WHERE scope=? AND id=?",
                (who.scope_key, ban_id),
            ).fetchone()
            if not row:
                raise ToolError("没有这个本群禁用编号。")
            db.execute("DELETE FROM learning_bans WHERE scope=? AND id=?", (who.scope_key, ban_id))
            db.execute(
                "UPDATE learning_pairs SET banned=0 WHERE scope=? AND reply_content IN "
                "(SELECT id FROM learning_contents WHERE scope=? AND digest=?)",
                (who.scope_key, who.scope_key, row["digest"]),
            )

    def clear(self, who: Identity) -> None:
        group_only(who)
        if not (who.group_role == "owner" or self.store.is_admin(who, owner=True)):
            raise ToolError("清空全部学习数据仅限本群群主或机器人总管理员；群管理员可逐条禁用回复。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for table in (
                "learning_pairs", "learning_contents", "learning_last",
                "learning_repeat_state", "learning_seen", "learning_bans",
            ):
                db.execute(f"DELETE FROM {table} WHERE scope=?", (who.scope_key,))

    @staticmethod
    def _blocked(db, scope: str, actor: str, content: LearningContent) -> bool:
        if db.execute(
            "SELECT 1 FROM learning_block_users WHERE scope=? AND actor=?", (scope, actor)
        ).fetchone():
            return True
        if content.kind != "text":
            return False
        folded = content.text.casefold()
        return any(row[0] in folded for row in db.execute(
            "SELECT word FROM learning_block_words WHERE scope=?", (scope,)
        ))

    @staticmethod
    def _insert_content(db, scope: str, content: LearningContent, now: float,
                        image_enabled: bool) -> int | None:
        existing = db.execute(
            "SELECT id FROM learning_contents WHERE scope=? AND digest=?", (scope, content.digest)
        ).fetchone()
        if existing:
            db.execute("UPDATE learning_contents SET updated=? WHERE id=?", (now, existing[0]))
            return existing[0]
        if content.kind == "image":
            if not image_enabled or content.image is None:
                return None
            count, size = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(image)),0) FROM learning_contents "
                "WHERE scope=? AND kind='image'", (scope,)
            ).fetchone()
            if count >= MAX_IMAGE_CONTENTS_PER_GROUP or size + len(content.image) > MAX_IMAGE_BYTES_PER_GROUP:
                return None
        else:
            count = db.execute(
                "SELECT COUNT(*) FROM learning_contents WHERE scope=? AND kind='text'", (scope,)
            ).fetchone()[0]
            if count >= MAX_TEXT_CONTENTS_PER_GROUP:
                return None
        return db.execute(
            "INSERT INTO learning_contents(scope,digest,kind,text,image,created,updated) "
            "VALUES(?,?,?,?,?,?,?)",
            (scope, content.digest, content.kind, content.text, content.image, now, now),
        ).lastrowid

    @staticmethod
    def _candidate(db, scope: str, bot: str, prompt_key: str, threshold: int,
                   addressed: bool, library_mode: str) -> LearningReply | None:
        if library_mode == "local":
            visibility = "p.scope=?"
            visibility_params = [scope]
        else:
            visibility = (
                "(p.scope=? OR (COALESCE(lc.library_mode,'public')='public' AND EXISTS "
                "(SELECT 1 FROM bot_scopes bs WHERE bs.scope=p.scope AND bs.bot=? AND bs.kind='group')))"
            )
            visibility_params = [scope, bot]
        rows = db.execute(
            "SELECT p.id,p.count,c.id AS content_id,c.kind,c.digest,c.text "
            "FROM learning_pairs p JOIN learning_contents c ON c.id=p.reply_content "
            "LEFT JOIN learning_config lc ON lc.scope=p.scope "
            f"WHERE {visibility} AND p.prompt_key=? AND p.banned=0 AND p.count>=? "
            "AND NOT EXISTS (SELECT 1 FROM learning_bans b WHERE b.scope=? AND b.digest=c.digest) "
            "ORDER BY p.count DESC,p.updated DESC LIMIT 50",
            (*visibility_params, prompt_key, 1 if addressed else threshold, scope),
        ).fetchall()
        blocked = [row[0] for row in db.execute(
            "SELECT word FROM learning_block_words WHERE scope=?", (scope,)
        )]
        rows = [
            row for row in rows
            if row["kind"] != "text" or all(word not in row["text"].casefold() for word in blocked)
        ]
        if not rows:
            return None
        weights = [max(1 if addressed else 0, row["count"] - 1) for row in rows]
        no_reply = 0 if addressed else len(rows)
        selected = random.choices([*rows, None], weights=[*weights, no_reply], k=1)[0]
        if selected is None:
            return None
        content = db.execute(
            "SELECT kind,text,image FROM learning_contents WHERE id=?", (selected["content_id"],)
        ).fetchone()
        if not content:
            return None
        return LearningReply(
            selected["id"], content["kind"], content["text"], content["image"], "learned"
        )

    @staticmethod
    def _repeat(db, scope: str, actor: str, content: LearningContent,
                threshold: int, repeat_enabled: bool, repeat_probability: int,
                interrupt_enabled: bool, interrupt_probability: int,
                now: float) -> LearningReply | None:
        row = db.execute(
            "SELECT * FROM learning_repeat_state WHERE scope=?", (scope,)
        ).fetchone()
        actor_tag = actor[:16]
        if row and row["digest"] == content.digest and now - row["updated"] <= CONTEXT_TTL:
            try:
                actors = set(json.loads(row["actors"]))
            except (ValueError, TypeError):
                actors = set()
            actors.add(actor_tag)
            count = row["count"] + 1
            replied = bool(row["replied"])
        else:
            actors, count, replied = {actor_tag}, 1, False
        trigger = bool(
            threshold and count >= threshold and len(actors) >= 2 and not replied
            and (repeat_enabled or interrupt_enabled)
        )
        db.execute(
            "INSERT INTO learning_repeat_state(scope,digest,actors,count,replied,updated) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
            "digest=excluded.digest,actors=excluded.actors,count=excluded.count,"
            "replied=excluded.replied,updated=excluded.updated",
            (scope, content.digest, json.dumps(sorted(actors)), count, int(replied or trigger), now),
        )
        if not trigger:
            return None
        if (interrupt_enabled and content.kind == "text"
                and interrupt_probability > 0
                and (interrupt_probability >= 100 or random.random() < interrupt_probability / 100)):
            return LearningReply(None, "text", text="打断复读！", reason="repeat")
        if (repeat_enabled and repeat_probability > 0
                and (repeat_probability >= 100 or random.random() < repeat_probability / 100)):
            return LearningReply(None, content.kind, content.text, content.image, "repeat")
        return None

    @staticmethod
    def _learn_pair(db, scope: str, prompt_id: int, prompt_key: str,
                    prompt_preview: str, reply_id: int, reply_digest: str,
                    now: float) -> None:
        if prompt_id == reply_id:
            return
        banned = bool(db.execute(
            "SELECT 1 FROM learning_bans WHERE scope=? AND digest=?", (scope, reply_digest)
        ).fetchone())
        db.execute(
            "INSERT INTO learning_pairs(scope,prompt_key,prompt_preview,reply_content,count,banned,created,updated) "
            "VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(scope,prompt_key,reply_content) DO UPDATE SET "
            "count=MIN(?,learning_pairs.count+1),updated=excluded.updated,banned=excluded.banned",
            (scope, prompt_key, prompt_preview, reply_id, int(banned), now, now, LEARN_MAX_COUNT),
        )

    @staticmethod
    def _trim_pairs(db, scope: str) -> None:
        count = db.execute(
            "SELECT COUNT(*) FROM learning_pairs WHERE scope=?", (scope,)
        ).fetchone()[0]
        if count > MAX_PAIRS_PER_GROUP:
            rows = db.execute(
                "SELECT id FROM learning_pairs WHERE scope=? "
                "ORDER BY banned DESC,count ASC,updated ASC LIMIT ?",
                (scope, count - MAX_PAIRS_PER_GROUP),
            ).fetchall()
            db.executemany("DELETE FROM learning_pairs WHERE id=?", ((row[0],) for row in rows))

    @staticmethod
    def _cleanup(db, scope: str, now: float) -> None:
        LearningStore._trim_pairs(db, scope)
        db.execute(
            "DELETE FROM learning_contents WHERE scope=? "
            "AND id NOT IN (SELECT reply_content FROM learning_pairs WHERE scope=?) "
            "AND id NOT IN (SELECT content FROM learning_last WHERE scope=?)",
            (scope, scope, scope),
        )

    def observe(self, who: Identity, content: LearningContent, message_id: str,
                *, addressed: bool = False, referenced: LearningContent | None = None,
                now: float | None = None) -> LearningReply | None:
        """Atomically learn one group message and possibly choose a reply."""
        group_only(who)
        if not message_id:
            return None
        now = time.time() if now is None else now
        scope = who.scope_key
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO bot_scopes(scope,bot,kind,updated) VALUES(?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET bot=excluded.bot,kind='group',updated=excluded.updated",
                (scope, who.bot, "group", now),
            )
            config = self._config(db, scope)
            if not config["enabled"] or self._blocked(db, scope, who.actor, content):
                return None
            seen = db.execute(
                "INSERT OR IGNORE INTO learning_seen VALUES(?,?,?)", (scope, message_id, now)
            )
            if not seen.rowcount:
                return None
            content_id = self._insert_content(
                db, scope, content, now, bool(config["image_enabled"])
            )
            if content_id is None:
                return None

            candidate = self._candidate(
                db, scope, who.bot, content.key, int(config["answer_threshold"]), addressed,
                str(config.get("library_mode", "public")),
            )
            repeated = self._repeat(
                db, scope, who.actor, content, int(config["repeat_threshold"]),
                bool(config.get("repeat_enabled", 1)),
                int(config.get("repeat_probability", 100)),
                bool(config.get("interrupt_repeat_enabled", 1)),
                int(config.get("interrupt_repeat_probability", 25)),
                now,
            )

            previous = None
            if referenced is not None and not self._blocked(db, scope, "", referenced):
                reference_id = self._insert_content(
                    db, scope, referenced, now, bool(config["image_enabled"])
                )
                if reference_id is not None:
                    previous = (reference_id, referenced.key, referenced.preview)
            if previous is None:
                last = db.execute(
                    "SELECT content,prompt_key,created FROM learning_last WHERE scope=?", (scope,)
                ).fetchone()
                if last and now - last["created"] <= CONTEXT_TTL:
                    preview = db.execute(
                        "SELECT kind,text FROM learning_contents WHERE id=?", (last["content"],)
                    ).fetchone()
                    if preview:
                        previous = (
                            last["content"], last["prompt_key"],
                            preview["text"][:60] if preview["kind"] == "text" else "[图片]",
                        )
            if previous is not None:
                self._learn_pair(
                    db, scope, previous[0], previous[1], previous[2],
                    content_id, content.digest, now,
                )
            db.execute(
                "INSERT INTO learning_last(scope,actor,content,prompt_key,message,created) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                "actor=excluded.actor,content=excluded.content,prompt_key=excluded.prompt_key,"
                "message=excluded.message,created=excluded.created",
                (scope, who.actor[:16], content_id, content.key, message_id, now),
            )
            self._trim_pairs(db, scope)
            return repeated or candidate
