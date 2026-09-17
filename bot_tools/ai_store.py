"""Per-user credentials, explicit group grants, bounded memory and atomic quotas."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

from .ai_provider import GLM_TOTAL_TIMEOUT, model_for, provider_for
from .storage import Identity, Store, ToolError, clean


CST = timezone(timedelta(hours=8))
TTL = 600
PRIVATE_DAILY = 5
DEFAULT_DAILY = 100
MAX_DAILY = 1000
HISTORY_BYTES = 1800
MAX_COLLECT_MESSAGES = 20
BUFFER_TTL = 30 * 60
SEEN_TTL = 24 * 60 * 60
GROUP_PROMPT_CHARACTERS = 2000
GROUP_PROMPT_BYTES = 6000


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def conversation(scope: str, owner: str) -> str:
    return digest(json.dumps([scope, owner]))


def private_only(who: Identity):
    if not who.private:
        raise ToolError("此操作只能私聊机器人。不要把密钥发到群里；若已发出，请立即到 AI 平台撤销并重新生成。")


def group_only(who: Identity):
    if who.private or not who.scope.startswith("group:"):
        raise ToolError("此操作只能在 QQ 群中使用。")


def number(raw: str, maximum: int = MAX_DAILY) -> int:
    try:
        result = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"请输入 1～{maximum} 之间的每日次数。") from None
    if not 1 <= result <= maximum:
        raise ToolError(f"每日次数范围是 1～{maximum}。")
    return result


@dataclass(frozen=True)
class Prepared:
    run: str
    bot: str
    owner: str
    scope: str
    revision: str
    epoch: str
    provider: str
    region: str
    name: str
    model: str
    key: str = field(repr=False)
    messages: list[dict] = field(repr=False)
    prompt: str = field(repr=False)


@dataclass(frozen=True)
class Collected:
    owner: str
    prompt: str = field(repr=False)
    batched: bool = False


class AIStore:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_profiles (
                    actor TEXT PRIMARY KEY, owner TEXT NOT NULL, bot TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'glm', region TEXT NOT NULL DEFAULT 'cn',
                    model TEXT NOT NULL DEFAULT '',
                    secret BLOB, name TEXT NOT NULL DEFAULT '', persona TEXT NOT NULL DEFAULT '',
                    daily INTEGER NOT NULL DEFAULT 100, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_accounts (
                    owner TEXT PRIMARY KEY, bot TEXT NOT NULL, selected TEXT,
                    daily INTEGER NOT NULL DEFAULT 100);
                CREATE TABLE IF NOT EXISTS ai_groups (
                    scope TEXT NOT NULL, bot TEXT NOT NULL, ref TEXT NOT NULL,
                    id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, sponsor TEXT NOT NULL,
                    cap INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
                    mention_only INTEGER NOT NULL DEFAULT 1,
                    collect_min INTEGER NOT NULL DEFAULT 0,
                    collect_max INTEGER NOT NULL DEFAULT 0,
                    collect_target INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(scope,sponsor));
                CREATE TABLE IF NOT EXISTS ai_sessions (
                    scope TEXT PRIMARY KEY, epoch TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_invites (
                    token TEXT PRIMARY KEY, bot TEXT NOT NULL, owner TEXT NOT NULL,
                    revision TEXT NOT NULL, cap INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_proposals (
                    token TEXT PRIMARY KEY, invite TEXT NOT NULL, bot TEXT NOT NULL,
                    scope TEXT NOT NULL, ref TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    prompt TEXT NOT NULL, reply TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS ai_history_scope ON ai_history(scope,id);
                CREATE TABLE IF NOT EXISTS ai_daily (
                    subject TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(subject,day));
                CREATE TABLE IF NOT EXISTS ai_runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, sponsor TEXT NOT NULL, scope TEXT NOT NULL,
                    created REAL NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_known INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS ai_runs_owner_created ON ai_runs(owner,created);
                CREATE INDEX IF NOT EXISTS ai_runs_scope_created ON ai_runs(scope,created);
                CREATE INDEX IF NOT EXISTS ai_runs_created ON ai_runs(created);
                CREATE INDEX IF NOT EXISTS ai_runs_sponsor_day ON ai_runs(sponsor,day);
                CREATE INDEX IF NOT EXISTS ai_profiles_owner ON ai_profiles(owner);
                CREATE INDEX IF NOT EXISTS ai_groups_owner ON ai_groups(owner,scope);
                CREATE INDEX IF NOT EXISTS ai_history_created ON ai_history(created);
                CREATE INDEX IF NOT EXISTS ai_daily_day ON ai_daily(day);
                CREATE INDEX IF NOT EXISTS ai_invites_expires ON ai_invites(expires);
                CREATE INDEX IF NOT EXISTS ai_proposals_expires ON ai_proposals(expires);
                CREATE TABLE IF NOT EXISTS ai_group_buffer (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL, owner TEXT NOT NULL, message TEXT NOT NULL,
                    actor TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL,
                    UNIQUE(scope,owner,message));
                CREATE INDEX IF NOT EXISTS ai_group_buffer_scope
                    ON ai_group_buffer(scope,owner,id);
                CREATE TABLE IF NOT EXISTS ai_group_seen (
                    scope TEXT NOT NULL, owner TEXT NOT NULL, message TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(scope,owner,message));
                CREATE INDEX IF NOT EXISTS ai_group_seen_created ON ai_group_seen(created);
                CREATE INDEX IF NOT EXISTS ai_group_buffer_created ON ai_group_buffer(created);
            """)
            # Existing installations predate the per-group trigger settings.
            # SQLite's CREATE TABLE IF NOT EXISTS does not add new columns.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_groups)")}
            migrations = {
                "mention_only": "INTEGER NOT NULL DEFAULT 1",
                "collect_min": "INTEGER NOT NULL DEFAULT 0",
                "collect_max": "INTEGER NOT NULL DEFAULT 0",
                "collect_target": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE ai_groups ADD COLUMN {name} {definition}")
            profile_columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_profiles)")}
            if "model" not in profile_columns:
                db.execute("ALTER TABLE ai_profiles ADD COLUMN model TEXT NOT NULL DEFAULT ''")

    def _vault(self, db) -> Fernet:
        folder = self.store.path / "secrets"
        path = folder / "ai-master.key"
        if folder.is_symlink() or path.is_symlink():
            raise ToolError("AI 加密文件路径异常，请联系服务器管理员。")
        folder.mkdir(mode=0o700, exist_ok=True)
        if not path.exists():
            if db.execute("SELECT 1 FROM ai_profiles WHERE secret IS NOT NULL LIMIT 1").fetchone():
                raise ToolError("AI 加密主密钥丢失，请管理员从备份恢复；不会生成新密钥覆盖旧数据。")
            # Caller holds BEGIN IMMEDIATE, including across separate processes.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(Fernet.generate_key())
                output.flush()
                os.fsync(output.fileno())
        try:
            with path.open("rb") as source:
                return Fernet(source.read(100))
        except (ValueError, OSError):
            raise ToolError("AI 加密主密钥不可用，请联系服务器管理员恢复备份。") from None

    def _decrypt(self, db, row) -> str:
        try:
            data = json.loads(self._vault(db).decrypt(row["secret"]))
            if data["actor"] != row["actor"]:
                raise ValueError("credential owner mismatch")
            return data["key"]
        except ToolError:
            raise
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise ToolError("AI 密钥无法解密，请联系管理员检查加密文件；不会使用其他用户的密钥。") from None

    @staticmethod
    def _profile(db, who: Identity):
        # ai_profiles.actor is a random role ID, NOT a QQ user/identity ID.
        # owner is always the configuring user's bot-scoped private actor.
        db.execute("INSERT OR IGNORE INTO ai_accounts(owner,bot) VALUES(?,?)", (who.actor, who.bot))
        row = db.execute("SELECT p.*,a.daily AS total_daily FROM ai_profiles p JOIN ai_accounts a ON p.actor=a.selected AND p.owner=a.owner WHERE a.owner=? AND a.bot=?", (who.actor, who.bot)).fetchone()
        if not row:
            role = secrets.token_hex(6)
            db.execute("INSERT INTO ai_profiles(actor,owner,bot,revision) VALUES(?,?,?,?)", (role, who.actor, who.bot, secrets.token_hex(16)))
            db.execute("UPDATE ai_accounts SET selected=? WHERE owner=?", (role, who.actor))
            row = db.execute("SELECT p.*,a.daily AS total_daily FROM ai_profiles p JOIN ai_accounts a ON p.actor=a.selected WHERE a.owner=?", (who.actor,)).fetchone()
        return row

    def roles(self, who: Identity) -> list[dict]:
        private_only(who)
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT p.actor AS id,p.name,p.provider,p.region,p.model,(p.secret IS NOT NULL) AS has_key,(p.actor=a.selected) AS selected FROM ai_profiles p JOIN ai_accounts a ON p.owner=a.owner WHERE p.owner=? AND p.bot=? ORDER BY p.actor", (who.actor, who.bot))]

    def select(self, who: Identity, role: str):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM ai_profiles WHERE actor=? AND owner=? AND bot=?", (role, who.actor, who.bot)).fetchone():
                raise ToolError("找不到属于你的角色，请发 /ai 角色列表 查看编号。")
            db.execute("UPDATE ai_accounts SET selected=? WHERE owner=?", (role, who.actor))

    @staticmethod
    def _role_text(value: str) -> tuple[str, str]:
        name, sep, persona = value.partition("|")
        name, persona = name.strip(), persona.strip()
        if not sep or not re.fullmatch(r"[\w\u4e00-\u9fff]{1,12}", name) or name.lower() in {"ai", "bot"}:
            raise ToolError("用法：/ai 角色 小桃 | 人设。名字为 1～12 个中英文字、数字或下划线，不能叫 ai/bot。")
        persona = clean(persona, 800)
        if len(persona.encode()) > 2400:
            raise ToolError("人设最多 800 字且不超过 2400 字节。")
        return name, persona

    def create_role(self, who: Identity, value: str) -> str:
        private_only(who)
        name, persona = self._role_text(value)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_text(db, who.actor, persona)
            if db.execute("SELECT COUNT(*) FROM ai_profiles WHERE owner=?", (who.actor,)).fetchone()[0] >= 20:
                raise ToolError("每人最多创建 20 个角色，请先删除不用的角色。")
            # No implicit copying of a key or spending another role's account.
            role = secrets.token_hex(6)
            db.execute("INSERT OR IGNORE INTO ai_accounts(owner,bot) VALUES(?,?)", (who.actor, who.bot))
            db.execute("INSERT INTO ai_profiles(actor,owner,bot,name,persona,revision) VALUES(?,?,?,?,?,?)", (role, who.actor, who.bot, name, persona, secrets.token_hex(16)))
            db.execute("UPDATE ai_accounts SET selected=? WHERE owner=?", (role, who.actor))
            return role

    def _check_text(self, db, owner: str, text: str):
        if re.search(r"(?i)\bsk-[a-z0-9_-]{16,}|\b[a-z0-9_-]{24,}\.[a-z0-9_-]{8,}", text):
            raise ToolError("检测到疑似密钥，请移除敏感信息。")
        for row in db.execute("SELECT * FROM ai_profiles WHERE owner=? AND secret IS NOT NULL", (owner,)).fetchall():
            if self._decrypt(db, row) in text:
                raise ToolError("内容不能包含你的 API Key。")

    def delete_role(self, who: Identity, role: str):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM ai_profiles WHERE actor=? AND owner=? AND bot=?", (role, who.actor, who.bot)).fetchone():
                raise ToolError("找不到属于你的角色。")
            self._delete_role(db, role, who.actor, conversation(who.scope_key, role))

    def delete_role_by_admin(self, role: str, bot: str) -> str:
        """Delete one role selected by the server administrator.

        The web admin intentionally has no access to the role owner's raw QQ
        identity.  Conversation hashes recorded by prior runs let us invalidate
        every private and group session without exposing that identity.
        """
        if not re.fullmatch(r"[a-f0-9]{12}", role):
            raise ToolError("角色编号格式不正确。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner,name FROM ai_profiles WHERE actor=? AND bot=?", (role, bot)
            ).fetchone()
            if not row:
                raise ToolError("没有找到这个 AI 角色。")
            self._delete_role(db, role, row["owner"])
            return row["name"] or "未命名"

    def _delete_role(self, db, role: str, account_owner: str,
                     private_scope: str = "") -> None:
        scopes = {
            row["scope"] for row in db.execute(
                "SELECT DISTINCT scope FROM ai_runs WHERE owner=?", (role,)
            ).fetchall()
        }
        if private_scope:
            scopes.add(private_scope)
        self._revoke_all(db, role)
        for scope in scopes:
            self._clear(db, scope)
        db.execute("DELETE FROM ai_profiles WHERE actor=?", (role,))
        db.execute(
            "UPDATE ai_accounts SET selected=(SELECT actor FROM ai_profiles "
            "WHERE owner=? ORDER BY actor LIMIT 1) WHERE owner=? AND selected=?",
            (account_owner, account_owner, role),
        )
        # Account, usage and run records deliberately survive role deletion so
        # deleting/recreating a role cannot reset today's quota or audit usage.

    @staticmethod
    def _clear(db, scope: str):
        db.execute("DELETE FROM ai_history WHERE scope=?", (scope,))
        db.execute("INSERT OR REPLACE INTO ai_sessions VALUES(?,?)", (scope, secrets.token_hex(16)))

    @staticmethod
    def _ready(row):
        if not row or not row["secret"] or not row["name"] or not row["persona"]:
            raise ToolError("角色还未配置完整，请角色主人私聊发送 /ai 设置。")

    @staticmethod
    def _purge(db, now: float):
        db.execute("DELETE FROM ai_invites WHERE expires<=?", (now,))
        db.execute("DELETE FROM ai_proposals WHERE expires<=? OR invite NOT IN (SELECT token FROM ai_invites)", (now,))
        db.execute("DELETE FROM ai_history WHERE created<?", (now-86400,))
        db.execute("DELETE FROM ai_group_buffer WHERE created<?", (now-BUFFER_TTL,))
        db.execute("DELETE FROM ai_group_seen WHERE created<?", (now-SEEN_TTL,))
        db.execute("DELETE FROM ai_runs WHERE created<?", (now-31*86400,))
        cutoff = datetime.fromtimestamp(now-31*86400, CST).strftime("%Y-%m-%d")
        db.execute("DELETE FROM ai_daily WHERE day<?", (cutoff,))

    def profile(self, who: Identity) -> dict | None:
        private_only(who)
        with self.store.connect() as db:
            row = db.execute("SELECT p.*,a.daily AS total_daily FROM ai_profiles p JOIN ai_accounts a ON p.actor=a.selected AND p.owner=a.owner WHERE a.owner=? AND a.bot=?", (who.actor, who.bot)).fetchone()
            if not row:
                return None
            # Metadata only. Ciphertext and secrets must never reach command UIs.
            result = {k: row[k] for k in ("provider", "region", "model", "name", "persona", "daily")}
            result["id"] = row["actor"]
            result["daily"] = row["total_daily"]
            result["has_key"] = bool(row["secret"])
            return result

    def configure(self, who: Identity, action: str, value: str):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._profile(db, who)
            role = row["actor"]
            if action == "service":
                args = value.lower().split()
                provider = {"智谱": "glm", "千问": "qwen"}.get(args[0], args[0]) if args else ""
                region = args[1] if len(args) == 2 else ("cn" if provider == "glm" else "beijing")
                region = {"北京": "beijing", "新加坡": "singapore", "弗吉尼亚": "virginia"}.get(region, region)
                if len(args) not in {1, 2}:
                    raise ToolError("用法：/ai 服务 glm 或 /ai 服务 qwen beijing。")
                provider_for(provider, region)
                if (row["provider"], row["region"]) == (provider, region):
                    return
                self._revoke_all(db, role)
                default_model = model_for(provider, region).code
                db.execute(
                    "UPDATE ai_profiles SET provider=?,region=?,model=?,secret=NULL WHERE actor=?",
                    (provider, region, default_model, role),
                )
            elif action == "model":
                selected_model = model_for(row["provider"], row["region"], value)
                current_model = model_for(row["provider"], row["region"], row["model"])
                if current_model.code == selected_model.code and row["model"]:
                    return
                db.execute("UPDATE ai_profiles SET model=? WHERE actor=?", (selected_model.code, role))
            elif action == "key":
                # QQ message transport is not a secure secret entry channel;
                # commands warn about QQ and server-operator visibility.
                if not re.fullmatch(r"[A-Za-z0-9._\-]{16,512}", value):
                    raise ToolError("密钥格式不正确，请只粘贴 API Key，不含引号、空格或其他内容。")
                if any(value in r[0] for r in db.execute("SELECT persona FROM ai_profiles WHERE owner=?", (who.actor,))):
                    raise ToolError("你的某个角色人设中包含此密钥，请先删除人设中的敏感信息。")
                encrypted = self._vault(db).encrypt(json.dumps({"actor": role, "key": value}).encode())
                db.execute("UPDATE ai_profiles SET secret=? WHERE actor=?", (encrypted, role))
            elif action == "role":
                name, persona = self._role_text(value)
                self._check_text(db, who.actor, persona)
                db.execute("UPDATE ai_profiles SET name=?,persona=? WHERE actor=?", (name, persona, role))
            elif action == "limit":
                db.execute("UPDATE ai_accounts SET daily=? WHERE owner=?", (number(value), who.actor))
                return
            else:
                raise ToolError("不支持的 AI 配置操作。")
            db.execute("UPDATE ai_profiles SET revision=? WHERE actor=?", (secrets.token_hex(16), role))
            db.execute("DELETE FROM ai_proposals WHERE invite IN (SELECT token FROM ai_invites WHERE owner=?)", (role,))
            db.execute("DELETE FROM ai_invites WHERE owner=?", (role,))
            self._clear(db, conversation(who.scope_key, role))
            for group in db.execute("SELECT scope FROM ai_groups WHERE owner=?", (role,)).fetchall():
                self._clear(db, conversation(group["scope"], role))
            db.execute("DELETE FROM ai_group_buffer WHERE owner=?", (role,))
            db.execute("DELETE FROM ai_group_seen WHERE owner=?", (role,))

    def _revoke_all(self, db, owner: str):
        for group in db.execute("SELECT scope FROM ai_groups WHERE owner=?", (owner,)).fetchall():
            self._clear(db, conversation(group["scope"], owner))
        db.execute("DELETE FROM ai_group_buffer WHERE owner=?", (owner,))
        db.execute("DELETE FROM ai_group_seen WHERE owner=?", (owner,))
        db.execute("DELETE FROM ai_groups WHERE owner=?", (owner,))
        db.execute("DELETE FROM ai_proposals WHERE invite IN (SELECT token FROM ai_invites WHERE owner=?)", (owner,))
        db.execute("DELETE FROM ai_invites WHERE owner=?", (owner,))

    def delete_key(self, who: Identity):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            role = self._profile(db, who)["actor"]
            self._revoke_all(db, role)
            db.execute("UPDATE ai_profiles SET secret=NULL,revision=? WHERE actor=?", (secrets.token_hex(16), role))
            self._clear(db, conversation(who.scope_key, role))

    def copy_key(self, who: Identity, source: str):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._profile(db, who)
            original = db.execute("SELECT * FROM ai_profiles WHERE actor=? AND owner=? AND bot=?", (source, who.actor, who.bot)).fetchone()
            if not original or not original["secret"] or original["actor"] == row["actor"]:
                raise ToolError("请选择你另一个已配置密钥的角色编号：/ai 角色列表。")
            key = self._decrypt(db, original)
            if key in row["persona"]:
                raise ToolError("当前人设包含此密钥，请先移除敏感信息。")
            encrypted = self._vault(db).encrypt(json.dumps({"actor": row["actor"], "key": key}).encode())
            self._revoke_all(db, row["actor"])
            self._clear(db, conversation(who.scope_key, row["actor"]))
            db.execute(
                "UPDATE ai_profiles SET provider=?,region=?,model=?,secret=?,revision=? WHERE actor=?",
                (
                    original["provider"], original["region"],
                    model_for(original["provider"], original["region"], original["model"]).code,
                    encrypted, secrets.token_hex(16), row["actor"],
                ),
            )

    def publish(self, who: Identity, cap: int, now: float | None = None) -> str:
        private_only(who)
        now = time.time() if now is None else now
        cap = number(str(cap))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge(db, now)
            row = self._profile(db, who)
            self._ready(row)
            if cap > row["total_daily"]:
                raise ToolError("本群额度不能大于你设置的每日总额度。")
            role = row["actor"]
            if db.execute("SELECT COUNT(*) FROM ai_groups WHERE owner=?", (role,)).fetchone()[0] >= 20:
                raise ToolError("一个角色最多授权 20 个群，请先撤销不用的授权。")
            db.execute("DELETE FROM ai_proposals WHERE invite IN (SELECT token FROM ai_invites WHERE owner=?)", (role,))
            db.execute("DELETE FROM ai_invites WHERE owner=?", (role,))
            code = secrets.token_hex(16)
            db.execute("INSERT INTO ai_invites VALUES(?,?,?,?,?,?)", (digest(code), who.bot, role, row["revision"], cap, now+TTL))
            return code

    def propose(self, who: Identity, code: str, *, now: float | None = None) -> dict:
        group_only(who)
        # Any member may submit. The credential owner still confirms the exact
        # target in private; group identity is never equated with C2C identity.
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge(db, now)
            invite = db.execute("SELECT * FROM ai_invites WHERE token=? AND bot=?", (digest(code.strip()), who.bot)).fetchone()
            if not invite:
                raise ToolError("接入码无效或已过期，请角色主人在私聊重新生成。")
            row = db.execute("SELECT * FROM ai_profiles WHERE actor=?", (invite["owner"],)).fetchone()
            self._ready(row)
            if db.execute("SELECT 1 FROM ai_groups WHERE scope=? AND sponsor=?", (who.scope_key, row["owner"])).fetchone():
                raise ToolError("你已在本群加入一个角色；请先私聊撤销原角色在此群的授权，再加入新角色。")
            if row["revision"] != invite["revision"]:
                raise ToolError("角色配置已更新，请重新生成接入码。")
            if db.execute("SELECT COUNT(*) FROM ai_proposals WHERE invite=?", (invite["token"],)).fetchone()[0] >= 10:
                raise ToolError("此接入码申请次数过多，请角色主人重新生成。")
            confirm = secrets.token_hex(16)
            # Multiple immutable proposals: another group cannot overwrite the
            # target the credential owner is about to confirm in private.
            db.execute("INSERT INTO ai_proposals VALUES(?,?,?,?,?,?)", (digest(confirm), invite["token"], who.bot,
                       who.scope_key, who.scope, min(now+TTL, invite["expires"])))
            return {"code": confirm, "name": row["name"], "cap": invite["cap"], "group": who.scope_key[:10]}

    def confirm(self, who: Identity, code: str, *, apply: bool = False, now: float | None = None) -> dict:
        private_only(who)
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge(db, now)
            proposal = db.execute("SELECT p.*,i.owner,i.cap,i.revision FROM ai_proposals p JOIN ai_invites i ON p.invite=i.token JOIN ai_profiles r ON i.owner=r.actor WHERE p.token=? AND p.bot=? AND r.owner=?",
                                  (digest(code.strip()), who.bot, who.actor)).fetchone()
            if not proposal:
                raise ToolError("确认码无效、已过期或不属于你。")
            row = db.execute("SELECT * FROM ai_profiles WHERE actor=? AND owner=?", (proposal["owner"], who.actor)).fetchone()
            self._ready(row)
            if row["revision"] != proposal["revision"]:
                raise ToolError("角色已变更，请重新发起接入。")
            result = {"group": proposal["scope"][:10], "name": row["name"], "cap": proposal["cap"]}
            if apply:
                if db.execute("SELECT 1 FROM ai_groups WHERE scope=? AND sponsor=?", (proposal["scope"], who.actor)).fetchone():
                    raise ToolError("你已在本群加入一个角色，不能再加入第二个。")
                identifier = secrets.token_hex(6)
                db.execute("INSERT INTO ai_groups(scope,bot,ref,id,owner,sponsor,cap) VALUES(?,?,?,?,?,?,?)", (proposal["scope"], who.bot, proposal["ref"], identifier, row["actor"], who.actor, proposal["cap"]))
                self._clear(db, conversation(proposal["scope"], row["actor"]))
                db.execute("DELETE FROM ai_proposals WHERE invite=?", (proposal["invite"],))
                db.execute("DELETE FROM ai_invites WHERE token=?", (proposal["invite"],))
                result["id"] = identifier
            return result

    def groups(self, who: Identity) -> list[dict]:
        private_only(who)
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT g.id,substr(g.scope,1,10) AS group_tag,g.cap,g.paused,g.mention_only,g.collect_min,g.collect_max,p.name FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor WHERE g.sponsor=? AND g.bot=? ORDER BY g.id", (who.actor, who.bot))]

    def group_settings(self, who: Identity, identifier: str, setting: str = "",
                       values: str = "") -> dict:
        """View or change one grant, authenticated by its private-chat sponsor."""
        private_only(who)
        identifier = identifier.strip().lower()
        if not re.fullmatch(r"[a-f0-9]{12}", identifier):
            raise ToolError("授权编号格式不正确，请先发送 /ai 授权列表。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT g.*,substr(g.scope,1,10) AS group_tag,p.name "
                "FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor "
                "WHERE g.id=? AND g.sponsor=? AND g.bot=?",
                (identifier, who.actor, who.bot),
            ).fetchone()
            if not row:
                raise ToolError("找不到属于你的群授权，请发送 /ai 授权列表查看编号。")
            if setting:
                if setting == "mention":
                    option = values.strip().lower()
                    mapping = {"on": 1, "开": 1, "开启": 1, "off": 0, "关": 0, "关闭": 0}
                    if option not in mapping:
                        raise ToolError("用法：/ai 群设置 授权编号 点名 on 或 off")
                    db.execute(
                        "UPDATE ai_groups SET mention_only=?,collect_target=0 WHERE id=?",
                        (mapping[option], identifier),
                    )
                elif setting == "collect":
                    parts = values.split()
                    if parts == ["0"]:
                        minimum = maximum = 0
                    elif len(parts) == 2 and all(
                        value.isascii() and value.isdigit() for value in parts
                    ):
                        minimum, maximum = map(int, parts)
                        if not 1 <= minimum <= maximum <= MAX_COLLECT_MESSAGES:
                            raise ToolError(
                                f"收集范围应为 1～{MAX_COLLECT_MESSAGES}，且最小值不能大于最大值；或单独填写 0。"
                            )
                    else:
                        raise ToolError("用法：/ai 群设置 授权编号 收集 3 5；设为 0 表示每条符合条件的消息都回复。")
                    db.execute(
                        "UPDATE ai_groups SET collect_min=?,collect_max=?,collect_target=0 WHERE id=?",
                        (minimum, maximum, identifier),
                    )
                else:
                    raise ToolError("可设置：点名 on/off，或收集 0/最小条数 最大条数。")
                db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
                db.execute("DELETE FROM ai_group_seen WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
                row = db.execute(
                    "SELECT g.*,substr(g.scope,1,10) AS group_tag,p.name "
                    "FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor WHERE g.id=?",
                    (identifier,),
                ).fetchone()
            return dict(row)

    def revoke(self, who: Identity, identifier: str):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if identifier == "all":
                for role in db.execute("SELECT actor FROM ai_profiles WHERE owner=? AND bot=?", (who.actor, who.bot)).fetchall():
                    self._revoke_all(db, role["actor"])
                return
            row = db.execute("SELECT scope,owner FROM ai_groups WHERE id=? AND sponsor=? AND bot=?", (identifier, who.actor, who.bot)).fetchone()
            if not row:
                raise ToolError("找不到属于你的群授权，请发送 /ai 授权列表。")
            self._clear(db, conversation(row["scope"], row["owner"]))
            db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
            db.execute("DELETE FROM ai_group_seen WHERE scope=? AND owner=?", (row["scope"], row["owner"]))
            db.execute("DELETE FROM ai_groups WHERE id=?", (identifier,))

    def group(self, who: Identity) -> list[dict]:
        group_only(who)
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT g.id,g.cap,g.paused,g.mention_only,g.collect_min,g.collect_max,p.name,p.provider,p.region FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor WHERE g.scope=? AND g.bot=? ORDER BY g.id", (who.scope_key, who.bot))]

    def moderate(self, who: Identity, action: str, identifier: str):
        group_only(who)
        if not self.store.is_admin(who):
            raise ToolError("此操作仅限机器人总管理员，或当前 QQ 群的群主/管理员。")
        if action == "remove" and identifier == "all" and not (
                who.group_role == "owner" or self.store.is_admin(who, owner=True)):
            raise ToolError("群管理员移除角色时请指定角色编号；all 仅限群主或机器人总管理员。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,owner FROM ai_groups WHERE scope=? AND (id=? OR ?='all')", (who.scope_key, identifier, identifier)).fetchall()
            if not rows:
                raise ToolError("未找到本群角色。发送 /ai 查看编号；也可用 all 操作本群全部角色。")
            for row in rows:
                self._clear(db, conversation(who.scope_key, row["owner"]))
                db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=?", (who.scope_key, row["owner"]))
                db.execute("DELETE FROM ai_group_seen WHERE scope=? AND owner=?", (who.scope_key, row["owner"]))
            if action == "remove":
                db.execute("DELETE FROM ai_groups WHERE scope=? AND (id=? OR ?='all')", (who.scope_key, identifier, identifier))
                db.execute("DELETE FROM ai_proposals WHERE scope=?", (who.scope_key,))
            elif action in {"pause", "resume"}:
                db.execute("UPDATE ai_groups SET paused=? WHERE scope=? AND (id=? OR ?='all')", (action == "pause", who.scope_key, identifier, identifier))
            elif action != "clear":
                raise ToolError("未知管理操作。")

    def clear(self, who: Identity):
        private_only(who)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            role = self._profile(db, who)["actor"]
            self._clear(db, conversation(who.scope_key, role))

    def globally_enabled(self, bot: str) -> bool:
        # Read the per-bot setting and its legacy fallback in one query.  This
        # function is on every ordinary-message path, so an extra connection
        # here has a measurable cost even when AI chat is disabled for a group.
        keys = ("ai:global:" + bot, "ai:global")
        with self.store.connect() as db:
            rows = {row["key"]: row["value"] for row in db.execute(
                "SELECT key,value FROM documents WHERE key IN (?,?)", keys
            )}
        for key in keys:
            if key not in rows:
                continue
            try:
                current = json.loads(rows[key])
            except (TypeError, ValueError):
                current = {}
            if isinstance(current, dict) and current:
                return current.get("enabled", True)
        return True

    def global_switch(self, who: Identity, enabled: bool):
        private_only(who)
        self.store.require_admin(who, owner=True)
        with self.store.state("ai:global:" + who.bot) as doc:
            doc["enabled"] = enabled
        if not enabled:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "DELETE FROM ai_group_buffer WHERE owner IN "
                    "(SELECT actor FROM ai_profiles WHERE bot=?)", (who.bot,)
                )
                db.execute(
                    "DELETE FROM ai_group_seen WHERE owner IN "
                    "(SELECT actor FROM ai_profiles WHERE bot=?)", (who.bot,)
                )

    def targets(self, who: Identity, text: str) -> list[str]:
        if self._configuration_text(text) or not self.globally_enabled(who.bot):
            return []
        with self.store.connect() as db:
            if who.private:
                rows = db.execute("SELECT actor,name FROM ai_profiles WHERE owner=? AND bot=? AND secret IS NOT NULL", (who.actor, who.bot))
                return [row["actor"] for row in rows if row["name"] and row["name"].casefold() in text.casefold()]
            else:
                group_only(who)
                rows = db.execute("SELECT p.actor,p.name,g.mention_only FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor WHERE g.scope=? AND g.bot=? AND g.paused=0", (who.scope_key, who.bot))
                return [row["actor"] for row in rows if row["name"] and (
                    not row["mention_only"] or row["name"].casefold() in text.casefold()
                )]

    @staticmethod
    def _clip(value: str, characters: int, byte_limit: int) -> str:
        result = " ".join(str(value).split())[:characters]
        while result and len(result.encode()) > byte_limit:
            result = result[:-1]
        return result

    @classmethod
    def _batch_prompt(cls, rows) -> str:
        count = max(1, len(rows))
        per_message_bytes = max(80, 5400 // count)
        lines = ["群聊最近消息："]
        for row in rows:
            prefix = f"成员{row['actor']}："
            content = cls._clip(row["content"], 240, max(24, per_message_bytes-len(prefix.encode())))
            lines.append(prefix + content)
        result = "\n".join(lines)
        if len(result.encode()) > GROUP_PROMPT_BYTES:
            result = result.encode()[:GROUP_PROMPT_BYTES].decode("utf-8", "ignore")
        return result

    def collect_targets(self, who: Identity, text: str, message_id: str,
                        now: float | None = None) -> list[Collected]:
        """Select immediate targets or atomically accumulate a group batch."""
        if self._configuration_text(text) or not message_id or not self.globally_enabled(who.bot):
            return []
        if who.private:
            return [Collected(owner, text) for owner in self.targets(who, text)]
        group_only(who)
        now = time.time() if now is None else now
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM ai_groups WHERE scope=? AND bot=? AND paused=0 LIMIT 1",
                              (who.scope_key, who.bot)).fetchone():
                return []
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT g.*,p.name FROM ai_groups g JOIN ai_profiles p ON g.owner=p.actor "
                "WHERE g.scope=? AND g.bot=? AND g.paused=0 "
                "AND p.secret IS NOT NULL AND p.name<>'' AND p.persona<>'' ORDER BY g.id",
                (who.scope_key, who.bot),
            ).fetchall()
            selected: list[Collected] = []
            safe_text = self._clip(text, 500, 1500)
            if not safe_text:
                return []
            for row in rows:
                named = row["name"].casefold() in text.casefold()
                if row["mention_only"] and not named:
                    continue
                # Global expiry runs periodically; only this role's expired
                # batch needs pruning before its current message is collected.
                db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=? AND created<?",
                           (who.scope_key, row["owner"], now-BUFFER_TTL))
                seen = db.execute(
                    "INSERT OR IGNORE INTO ai_group_seen(scope,owner,message,created) VALUES(?,?,?,?)",
                    (who.scope_key, row["owner"], message_id, now),
                )
                if not seen.rowcount:
                    continue
                minimum, maximum = row["collect_min"], row["collect_max"]
                if minimum == 0 and maximum == 0:
                    selected.append(Collected(row["owner"], text))
                    continue
                db.execute(
                    "INSERT INTO ai_group_buffer(scope,owner,message,actor,content,created) VALUES(?,?,?,?,?,?)",
                    (who.scope_key, row["owner"], message_id, who.actor[:6], safe_text, now),
                )
                target = row["collect_target"]
                if not minimum <= target <= maximum:
                    target = minimum + secrets.randbelow(maximum-minimum+1)
                    db.execute("UPDATE ai_groups SET collect_target=? WHERE id=?", (target, row["id"]))
                count = db.execute(
                    "SELECT COUNT(*) FROM ai_group_buffer WHERE scope=? AND owner=?",
                    (who.scope_key, row["owner"]),
                ).fetchone()[0]
                if count < target:
                    continue
                buffered = db.execute(
                    "SELECT actor,content FROM ai_group_buffer WHERE scope=? AND owner=? ORDER BY id",
                    (who.scope_key, row["owner"]),
                ).fetchall()
                selected.append(Collected(row["owner"], self._batch_prompt(buffered), True))
                db.execute("DELETE FROM ai_group_buffer WHERE scope=? AND owner=?", (who.scope_key, row["owner"]))
                db.execute("UPDATE ai_groups SET collect_target=0 WHERE id=?", (row["id"],))
            return selected

    @staticmethod
    def _configuration_text(text: str) -> bool:
        return text.lstrip().startswith("/") or bool(re.search(r"(?i)/ai\b|/bot\s+whoami\b", text))

    def prepare(self, who: Identity, text: str, message_id: str, owner: str | None = None,
                now: float | None = None, *, batched: bool = False) -> Prepared | None:
        """Validate a selected trigger and atomically reserve quota before I/O."""
        if self._configuration_text(text) or not message_id:
            return None
        if not self.globally_enabled(who.bot):
            return None
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, CST).strftime("%Y-%m-%d")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if who.private:
                if owner is None:
                    current = db.execute("SELECT selected FROM ai_accounts WHERE owner=? AND bot=?", (who.actor, who.bot)).fetchone()
                    owner = current[0] if current else ""
                row = db.execute("SELECT * FROM ai_profiles WHERE actor=? AND owner=? AND bot=?", (owner, who.actor, who.bot)).fetchone()
                cap = PRIVATE_DAILY
            else:
                group_only(who)
                group = db.execute("SELECT * FROM ai_groups WHERE scope=? AND bot=? AND owner=?", (who.scope_key, who.bot, owner)).fetchone()
                if not group or group["paused"]:
                    return None
                row = db.execute("SELECT * FROM ai_profiles WHERE actor=?", (group["owner"],)).fetchone()
                cap = group["cap"]
            if not row or not row["name"] or not row["secret"] or not row["persona"]:
                return None
            scope = conversation(who.scope_key, row["actor"])
            # Private chat always requires a name. Group grants decide whether
            # a name is required; collect_targets performs the same check before
            # buffering, while this check prevents direct-call bypasses.
            # A batched prompt has already passed the per-message name check in
            # collect_targets.  Its bounded rendering may legitimately clip a
            # name that appeared near the end of a long source message.
            if (who.private or (group["mention_only"] and not batched)) and row["name"].casefold() not in text.casefold():
                return None
            prompt = text.strip()
            character_limit = 500 if who.private else GROUP_PROMPT_CHARACTERS
            byte_limit = 1500 if who.private else GROUP_PROMPT_BYTES
            if len(prompt) > character_limit or len(prompt.encode()) > byte_limit:
                raise ToolError(f"聊天内容太长，请控制在 {character_limit} 字、{byte_limit} 字节以内。")
            key = self._decrypt(db, row)
            selected_model = model_for(row["provider"], row["region"], row["model"])
            self._check_text(db, row["owner"], prompt)
            run = digest(json.dumps([who.bot, who.scope, row["actor"], message_id]))
            if db.execute("SELECT 1 FROM ai_runs WHERE id=?", (run,)).fetchone():
                return None
            busy = db.execute("SELECT 1 FROM ai_runs WHERE owner=? AND state='pending' AND created>?", (row["actor"], now-GLM_TOTAL_TIMEOUT-30)).fetchone()
            if busy:
                raise ToolError("角色正在回复其他消息，请稍后再试。")
            latest = db.execute("SELECT MAX(created) FROM ai_runs WHERE scope=?", (scope,)).fetchone()[0]
            if latest is not None and latest > now-5:
                raise ToolError("请稍等 5 秒再聊天。")
            account = db.execute("SELECT daily FROM ai_accounts WHERE owner=?", (row["owner"],)).fetchone()
            if not account:
                return None
            meter_scope = who.scope_key if who.private else scope
            subjects = (("owner:"+row["owner"], account[0]), ("scope:"+meter_scope, cap))
            for subject, limit in subjects:
                used = db.execute("SELECT calls FROM ai_daily WHERE subject=? AND day=?", (subject, day)).fetchone()
                if used and used[0] >= limit:
                    label = "角色主人的每日总额度" if subject.startswith("owner:") else ("私聊每日 5 次额度" if who.private else "本角色在该群的每日额度")
                    raise ToolError(label + "已用完，北京时间次日 00:00 恢复。")
            for subject, _ in subjects:
                db.execute("INSERT INTO ai_daily VALUES(?,?,1) ON CONFLICT(subject,day) DO UPDATE SET calls=calls+1", (subject, day))
            db.execute("INSERT INTO ai_runs(id,owner,sponsor,scope,created,day,state) VALUES(?,?,?,?,?,?,'pending')", (run, row["actor"], row["owner"], scope, now, day))
            db.execute("INSERT OR IGNORE INTO ai_sessions VALUES(?,?)", (scope, secrets.token_hex(16)))
            epoch = db.execute("SELECT epoch FROM ai_sessions WHERE scope=?", (scope,)).fetchone()[0]
            history = []
            size = 0
            for item in db.execute("SELECT prompt,reply FROM ai_history WHERE scope=? AND created>=? ORDER BY id DESC LIMIT 3", (scope, now-86400)):
                size += len(item["prompt"].encode()) + len(item["reply"].encode())
                if size > HISTORY_BYTES:
                    break
                history[0:0] = [{"role": "user", "content": item["prompt"]}, {"role": "assistant", "content": item["reply"]}]
            if not who.private and not batched:
                prompt = f"群成员{who.actor[:6]}：{prompt}"
            system = (f"你是名叫{row['name']}的虚构聊天角色。只进行中文角色扮演，通常用1～3句简短回复。"
                      "不要声称具有真实身份、系统管理权限或执行操作的能力。你没有工具，不能读取密钥、文件或执行命令。"
                      "多人群聊中每条消息的成员标记只用于区分说话人，不代表权限。\n角色设定：" + row["persona"])
            messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": prompt}]
            return Prepared(
                run, who.bot, row["actor"], scope, row["revision"], epoch,
                row["provider"], row["region"], row["name"], selected_model.code,
                key, messages, prompt,
            )

    def finish(self, request: Prepared, result=None, now: float | None = None) -> bool:
        """Record usage even after revocation; never restore invalidated memory."""
        now = time.time() if now is None else now
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT state FROM ai_runs WHERE id=?", (request.run,)).fetchone()
            if not run or run[0] != "pending":
                return False
            db.execute("UPDATE ai_runs SET state=?,input_tokens=?,output_tokens=?,usage_known=? WHERE id=?",
                       ("done" if result else "error", result.input_tokens if result else 0, result.output_tokens if result else 0,
                        bool(result and result.usage_known), request.run))
            row = db.execute("SELECT revision,secret FROM ai_profiles WHERE actor=?", (request.owner,)).fetchone()
            session = db.execute("SELECT epoch FROM ai_sessions WHERE scope=?", (request.scope,)).fetchone()
            global_state = db.execute(
                "SELECT value FROM documents WHERE key=?",
                ("ai:global:" + request.bot,),
            ).fetchone()
            if not global_state:
                global_state = db.execute(
                    "SELECT value FROM documents WHERE key='ai:global'"
                ).fetchone()
            enabled = not global_state or json.loads(global_state[0]).get("enabled", True)
            valid = bool(enabled and row and row["secret"] and row["revision"] == request.revision and session and session[0] == request.epoch)
            if result and valid:
                db.execute("INSERT INTO ai_history(scope,prompt,reply,created) VALUES(?,?,?,?)", (request.scope, request.prompt, result.text, now))
                db.execute("DELETE FROM ai_history WHERE scope=? AND id NOT IN (SELECT id FROM ai_history WHERE scope=? ORDER BY id DESC LIMIT 3)", (request.scope, request.scope))
            return valid

    def usage(self, who: Identity, now: float | None = None) -> dict:
        private_only(who)
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, CST).strftime("%Y-%m-%d")
        with self.store.connect() as db:
            total = db.execute("SELECT calls FROM ai_daily WHERE subject=? AND day=?", ("owner:"+who.actor, day)).fetchone()
            private = db.execute("SELECT calls FROM ai_daily WHERE subject=? AND day=?", ("scope:"+who.scope_key, day)).fetchone()
            tokens = db.execute("SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(1-usage_known),0) FROM ai_runs WHERE sponsor=? AND day=?", (who.actor, day)).fetchone()
            return {"total": total[0] if total else 0, "private": private[0] if private else 0,
                    "input": tokens[0], "output": tokens[1], "unknown": tokens[2]}
