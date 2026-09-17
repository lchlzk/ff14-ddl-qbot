"""Gallery routing and storage, independent of QQ transport and image processing.

Originals live in content-addressed local files; legacy SQLite BLOBs stay readable
during migration. A future object-store backend need not change group permissions.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from dataclasses import dataclass

from .storage import Identity, Store, ToolError
from .media_objects import LocalObjects, configured_objects

LOCAL_LIMIT = 100
PAGE_SIZE = 20
SIZE_SQL = "CASE WHEN object_key<>'' THEN byte_size ELSE length(image) END"
SHARED_PUBLIC_SCOPE = hashlib.sha256(
    json.dumps(["gallery:public:shared:v2"]).encode()
).hexdigest()


def read_row(store: Store, row) -> bytes:
    return _objects(store).read(row["object_key"]) if row["object_key"] else bytes(row["image"])


def _objects(store: Store):
    # Passing this module's LocalObjects keeps the local backend patchable in
    # migration tests while production can select COS through the environment.
    return configured_objects(store.path, LocalObjects)


def migrate_legacy(store: Store, limit: int = 10) -> int:
    """Resumable migration with a verified pre-migration database backup.

    Each original is flushed to disk and read back before its BLOB is cleared.
    Old and new rows remain readable throughout; no VACUUM or file GC is run.
    """
    if store.document("gallery:storage").get("mode") == "sqlite":
        return 0
    with store.connect() as db:
        ids = [r[0] for r in db.execute(
            "SELECT id FROM gallery WHERE object_key='' AND length(image)>0 LIMIT ?", (limit,))]
    if not ids:
        return 0
    marker = store.document("gallery:migration-backup")
    backup_name = marker.get("file", "")
    backup_root = store.path / "backups"
    if (not backup_name or backup_name != Path(backup_name).name
            or not (backup_root / backup_name).is_file()):
        backup_root.mkdir(exist_ok=True)
        backup_name = "before-gallery-files-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + secrets.token_hex(4) + ".sqlite3"
        backup_path = backup_root / backup_name
        backup_path.touch(mode=0o600, exist_ok=False)
        destination = sqlite3.connect(backup_path)
        try:
            with store.connect() as source:
                source.backup(destination)
            if any(r[0] != "ok" for r in destination.execute("PRAGMA quick_check")):
                raise ToolError("图库迁移备份校验失败，原数据库未改动。")
        finally:
            destination.close()
        with store.state("gallery:migration-backup") as doc:
            doc["file"] = backup_name
    moved = 0
    backend = _objects(store)
    for item_id in ids:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Recheck under the writer lock: rollback may have paused migration
            # since the batch was selected, and must not race an in-flight batch.
            mode = db.execute("SELECT value FROM documents WHERE key='gallery:storage'").fetchone()
            if mode and json.loads(mode[0]).get("mode") == "sqlite":
                break
            row = db.execute("SELECT image,object_key FROM gallery WHERE id=?", (item_id,)).fetchone()
            if not row or row["object_key"] or not row["image"]:
                continue
            original = bytes(row["image"])
            key = backend.put(original)
            if backend.read(key) != original:
                raise ToolError("图片迁移校验失败，原图片仍保留在数据库。")
            db.execute("UPDATE gallery SET object_key=?,byte_size=?,image=X'' WHERE id=?",
                       (key, len(original), item_id))
            moved += 1
    return moved


def restore_blobs(store: Store) -> int:
    """Explicit server-only rollback before running an older BLOB-only image."""
    with store.state("gallery:storage") as doc:
        doc["mode"] = "sqlite"
    total = 0
    while True:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,object_key FROM gallery WHERE object_key<>'' LIMIT 1").fetchone()
            if not row:
                return total
            original = _objects(store).read(row["object_key"])
            if hashlib.sha256(original).hexdigest() != row["object_key"]:
                raise ToolError("原图校验失败，已停止回退；尚未处理的文件引用仍保留。")
            db.execute("UPDATE gallery SET image=?,object_key='',byte_size=0 WHERE id=?", (original, row["id"]))
            total += 1


@dataclass(frozen=True)
class GalleryTarget:
    scope: str
    public: bool = False

    @property
    def label(self) -> str:
        return "公共图库" if self.public else "本会话图库"


def can_switch(store: Store, who: Identity) -> bool:
    # A private gallery is a QQ-group-only option. Private chats always use the
    # bot-wide public gallery, including for the bot superadmin.
    return (
        not who.private
        and who.scope.startswith("group:")
        and (who.group_role == "owner" or store.is_admin(who, owner=True))
    )


def target_from_doc(who: Identity, doc: dict) -> GalleryTarget:
    mode = doc.get("gallery_mode")
    # QQ groups share the server-wide public gallery by default. Private chats are
    # always routed to that same public gallery and cannot opt into local
    # storage. An explicit group-local choice is always preserved.
    private_public = who.private or who.scope.startswith("private:")
    default_public = (
        not private_public
        and who.scope.startswith("group:")
        and mode not in {"public", "local"}
    )
    if mode == "public" or private_public or default_public:
        return GalleryTarget(SHARED_PUBLIC_SCOPE, True)
    return GalleryTarget(who.scope_key)


def _migrate_public_scope(db, who: Identity) -> None:
    """Move this bot's former public gallery into the server-wide shared pool."""
    legacy = hashlib.sha256(json.dumps([who.bot, "gallery:public:v1"]).encode()).hexdigest()
    if legacy == SHARED_PUBLIC_SCOPE:
        return
    db.execute(
        "UPDATE OR IGNORE gallery SET scope=?,visibility='public' WHERE scope=?",
        (SHARED_PUBLIC_SCOPE, legacy),
    )
    # Rows still on the old scope are exact category/digest duplicates.
    db.execute("DELETE FROM gallery WHERE scope=?", (legacy,))


def _target(db, who: Identity) -> GalleryTarget:
    _migrate_public_scope(db, who)
    row = db.execute("SELECT value FROM documents WHERE key=?", (who.scope_key,)).fetchone()
    return target_from_doc(who, json.loads(row[0]) if row else {})


def current(store: Store, who: Identity) -> GalleryTarget:
    with store.connect() as db:
        return _target(db, who)


def _checked_target(db, who: Identity, expected: GalleryTarget | None) -> GalleryTarget:
    active = _target(db, who)
    if expected is not None and active != expected:
        raise ToolError("图库模式已改变，本次未继续操作；请重新发送命令。")
    return active


def add(store: Store, who: Identity, category: str, image: bytes,
        expected: GalleryTarget | None = None) -> int:
    digest = hashlib.sha256(image).hexdigest()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        target = _checked_target(db, who, expected)
        duplicate = db.execute("SELECT id FROM gallery WHERE scope=? AND category=? AND digest=?",
                               (target.scope, category, digest)).fetchone()
        if duplicate:
            return duplicate[0]
        if not target.public and db.execute("SELECT COUNT(*) FROM gallery WHERE scope=?",
                                            (target.scope,)).fetchone()[0] >= LOCAL_LIMIT:
            raise ToolError("本会话图库已满（100 张），请联系管理员移除不再使用的图片。")
        # No count cap or expiry for public images. Existing cooldown and quota
        # are enforced by the command layer; storage exhaustion must fail safely.
        backend_mode = db.execute("SELECT value FROM documents WHERE key='gallery:storage'").fetchone()
        if backend_mode and json.loads(backend_mode[0]).get("mode") == "sqlite":
            return db.execute("INSERT INTO gallery(scope,category,digest,image,visibility) VALUES (?,?,?,?,?)",
                              (target.scope, category, digest, image, "public" if target.public else "local")).lastrowid
        key = _objects(store).put(image)
        return db.execute("INSERT INTO gallery(scope,category,digest,image,object_key,byte_size,visibility) VALUES (?,?,?,X'',?,?,?)",
                          (target.scope, category, digest, key, len(image), "public" if target.public else "local")).lastrowid


def get(store: Store, who: Identity, category: str,
        expected: GalleryTarget | None = None) -> bytes | None:
    with store.connect() as db:
        db.execute("BEGIN")
        target = _checked_target(db, who, expected)
        count = db.execute("SELECT COUNT(*) FROM gallery WHERE scope=? AND category=?",
                           (target.scope, category)).fetchone()[0]
        if not count:
            return None
        # Select one indexed row, not a Python list of every public image ID.
        row = db.execute("SELECT image,object_key FROM gallery WHERE scope=? AND category=? ORDER BY id LIMIT 1 OFFSET ?",
                         (target.scope, category, secrets.randbelow(count))).fetchone()
    return read_row(store, row)


def listing(store: Store, who: Identity, category: str | None, page: int):
    with store.connect() as db:
        db.execute("BEGIN")
        target = _target(db, who)
        if category is None:
            total = db.execute("SELECT COUNT(DISTINCT category) FROM gallery WHERE scope=?",
                               (target.scope,)).fetchone()[0]
            rows = db.execute("SELECT category,COUNT(*) FROM gallery WHERE scope=? GROUP BY category ORDER BY category LIMIT ? OFFSET ?",
                              (target.scope, PAGE_SIZE, (page-1)*PAGE_SIZE)).fetchall()
        else:
            total = db.execute("SELECT COUNT(*) FROM gallery WHERE scope=? AND category=?",
                               (target.scope, category)).fetchone()[0]
            rows = db.execute("SELECT id FROM gallery WHERE scope=? AND category=? ORDER BY id LIMIT ? OFFSET ?",
                              (target.scope, category, PAGE_SIZE, (page-1)*PAGE_SIZE)).fetchall()
        return target, rows, total


def stats(store: Store, who: Identity):
    with store.connect() as db:
        db.execute("BEGIN")
        target = _target(db, who)
        row = db.execute(f"SELECT COUNT(*),COALESCE(SUM({SIZE_SQL}),0) FROM gallery WHERE scope=?",
                         (target.scope,)).fetchone()
        return target, row[0], row[1]


def delete(store: Store, who: Identity, item_id: int):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        target = _target(db, who)
        # A QQ group owner controls their own gallery, not shared public data.
        store.require_admin(who, owner=target.public)
        if not db.execute("DELETE FROM gallery WHERE id=? AND scope=?", (item_id, target.scope)).rowcount:
            raise ToolError("当前会话没有此图片，或编号不属于当前选择的图库。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="服务器图库迁移与旧版兼容回退")
    parser.add_argument("action", choices=("migrate", "restore-blobs"))
    action = parser.parse_args().action
    store = Store()
    if action == "restore-blobs":
        print(f"已将 {restore_blobs(store)} 张原图恢复为旧版可读取的数据库格式，文件副本仍保留。")
    else:
        with store.state("gallery:storage") as doc:
            doc["mode"] = "files"
        total = 0
        while (count := migrate_legacy(store)):
            total += count
        print(f"已完成 {total} 张原图的文件存储迁移，迁移前备份仍保留。")
