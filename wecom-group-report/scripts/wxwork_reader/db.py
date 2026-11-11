"""Read a WeCom (企业微信) group conversation from decrypted local databases.

Decryption happens into a temporary directory that is removed at process exit;
only the exported messages.json/txt/report stay behind.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .crypto import PAGE_SZ, decrypt_database, is_encrypted_page1, is_plain_sqlite
from .keyfind import extract_keys

TZ = timezone(timedelta(hours=8))
DB_NAMES = ("message.db", "session.db", "user.db")

TEXT_TYPES = {0, 2}
TYPE_LABEL = {
    0: "文本", 2: "文本", 4: "图片", 7: "语音", 15: "文件", 20: "其他",
    36: "合并转发", 40: "通话", 503: "状态", 1002: "系统", 1011: "会议",
}


# ---------- protobuf helpers ----------
def _varint(buf: bytes, i: int) -> tuple[int, int]:
    val = shift = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val, i


def _fields(buf: bytes) -> list[tuple[int, object]]:
    out, i = [], 0
    while i < len(buf):
        try:
            key, i = _varint(buf, i)
        except IndexError:
            break
        field, wire = key >> 3, key & 7
        try:
            if wire == 2:
                n, i = _varint(buf, i)
                out.append((field, buf[i:i + n]))
                i += n
            elif wire == 0:
                v, i = _varint(buf, i)
                out.append((field, v))
            elif wire == 5:
                out.append((field, buf[i:i + 4]))
                i += 4
            elif wire == 1:
                out.append((field, buf[i:i + 8]))
                i += 8
            else:
                break
        except IndexError:
            break
    return out


def _utf8(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="ignore")


RUN_RE = re.compile(
    r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9]"
    r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9 .,;:!?，。！？、；："
    r"“”‘’（）【】《》%+\-_/@#\n]*")


def clean_text(text: str) -> str:
    """Strip protobuf length prefixes / control bytes, keep readable runs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not any(ord(c) < 32 and c not in "\n\t" for c in text):
        return text.strip()
    runs = [m.group(0).strip() for m in RUN_RE.finditer(text)]
    runs = [r for r in runs if len(r) >= 2]
    return "\n".join(runs).strip()


def parse_content(content_type: int, raw: Optional[bytes]) -> tuple[str, str]:
    """Return (type_label, text) for a WeCom message content blob."""
    label = TYPE_LABEL.get(content_type, f"类型{content_type}")
    if not raw:
        return label, ""
    if isinstance(raw, str):
        return label, raw.strip()

    if content_type in TEXT_TYPES:
        # structure: outer.f1 -> inner.f2 -> sub.f1 = text
        # a message may contain several outer.f1 entries (text, then @mention list)
        texts: list[str] = []
        for f, v in _fields(raw):
            if f == 1 and isinstance(v, bytes):
                for f2, v2 in _fields(v):
                    if f2 == 2 and isinstance(v2, bytes):
                        for f3, v3 in _fields(v2):
                            if f3 == 1 and isinstance(v3, bytes):
                                t = _utf8(v3)
                                if t.strip():
                                    texts.append(t)
        if not texts:
            chunks = [v for f, v in _fields(raw) if isinstance(v, bytes)]
            cand = max(chunks, key=len) if chunks else b""
            texts = [_utf8(cand)]
        text = max(texts, key=lambda t: len(t.strip())) if len(texts) > 1 else texts[0]
        # prefer the longest run: the @mention blob is short and useless
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            cleaned = clean_text(text)
            if cleaned:
                text = cleaned
        return "文本", text.strip()

    if content_type == 15:
        name, size = "", 0
        for f, v in _fields(raw):
            if f == 2 and isinstance(v, bytes) and not name:
                name = _utf8(v)
            if f == 4 and isinstance(v, int):
                size = v
        return "文件", f"{name}" + (f"（{size // 1024} KB）" if size else "")

    if content_type == 36:
        # merged-forward chat record: several f1 sub-messages, text at sub.f101
        parts = []
        for f, v in _fields(raw):
            if f == 1 and isinstance(v, bytes):
                for f2, v2 in _fields(v):
                    if f2 == 101 and isinstance(v2, bytes):
                        t = _utf8(v2).strip()
                        if t:
                            parts.append(t)
        return "合并转发", " | ".join(parts[:20])

    if content_type == 4:
        return "图片", ""
    if content_type == 7:
        return "语音", ""
    if content_type == 1011:
        return "会议", ""
    if content_type == 1002:
        return "系统", "群成员变动"
    return label, ""


# ---------- decryption ----------
def find_db_dir(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    candidates = []
    for root in (os.path.join(os.path.expanduser("~"), "Documents", "WXWork"), r"D:\vxwork\WXWork", r"D:\WXWork"):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            d = os.path.join(root, name, "Data")
            if os.path.isfile(os.path.join(d, "message.db")):
                candidates.append(d)
    if not candidates:
        raise RuntimeError("找不到企业微信 Data 目录，请用 --db-dir 指定")
    candidates.sort(key=lambda d: os.path.getmtime(os.path.join(d, "message.db")), reverse=True)
    return candidates[0]


class PlainDBs:
    """Decrypt needed DBs into a temp dir, remove on exit."""

    def __init__(self, db_dir: str, names: Iterable[str] = DB_NAMES):
        self.db_dir = db_dir
        self.names = [n for n in names if os.path.isfile(os.path.join(db_dir, n))]
        self.tmp = tempfile.mkdtemp(prefix="wxwork_plain_")
        self.paths: dict[str, str] = {}
        self.checks: dict[str, str] = {}
        atexit.register(self.cleanup)

    def open(self) -> "PlainDBs":
        salts, srcs = {}, {}
        for n in self.names:
            p = os.path.join(self.db_dir, n)
            with open(p, "rb") as f:
                page1 = f.read(PAGE_SZ)
            if is_plain_sqlite(page1):
                shutil.copy2(p, os.path.join(self.tmp, n))
                self.paths[n] = os.path.join(self.tmp, n)
            else:
                if is_encrypted_page1(page1):
                    salts[page1[:16].hex()] = p
                    srcs[n] = page1[:16].hex()
        if salts:
            keys = extract_keys(salts)
            for n, salt in srcs.items():
                out = os.path.join(self.tmp, n)
                decrypt_database(os.path.join(self.db_dir, n), out, keys[salt])
                self.paths[n] = out
        for n, p in self.paths.items():
            con = sqlite3.connect(p)
            try:
                self.checks[n] = con.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                con.close()
        return self

    def path(self, name: str) -> str:
        return self.paths[name]

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------- queries ----------
def find_group(session_db: str, name: str) -> dict:
    con = sqlite3.connect(session_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, name, roomname_remark, last_message_time, create_time FROM conversation_table "
            "WHERE name = ? OR roomname_remark = ?", (name, name)).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT id, name, roomname_remark, last_message_time, create_time FROM conversation_table "
                "WHERE name LIKE ? OR roomname_remark LIKE ?", (f"%{name}%", f"%{name}%")).fetchall()
        if not rows:
            raise RuntimeError(f"没有找到群「{name}」")
        if len(rows) > 1:
            exact = [r for r in rows if r["name"] == name or r["roomname_remark"] == name]
            rows = exact or rows
        r = rows[0]
        return {"id": r["id"], "name": r["roomname_remark"] or r["name"],
                "last_message_time": r["last_message_time"]}
    finally:
        con.close()


def load_users(user_db: str) -> dict[int, str]:
    con = sqlite3.connect(user_db)
    con.row_factory = sqlite3.Row
    users: dict[int, str] = {}
    try:
        for r in con.execute("SELECT id, name, real_name, account, external_corp_name, position FROM user_table"):
            name = (r["real_name"] or r["name"] or r["account"] or "").strip()
            if name:
                users[int(r["id"])] = name
        try:
            for r in con.execute("SELECT user_id, remarks, real_remarks, corp_remark FROM external_user_relation_v3"):
                uid = int(r["user_id"])
                if uid not in users:
                    nm = (r["real_remarks"] or r["remarks"] or r["corp_remark"] or "").strip()
                    if nm:
                        users[uid] = nm
        except sqlite3.Error:
            pass
        return users
    finally:
        con.close()


def load_member_nicks(session_db: str, group_id: str) -> dict[int, str]:
    con = sqlite3.connect(session_db)
    con.row_factory = sqlite3.Row
    out: dict[int, str] = {}
    try:
        try:
            for r in con.execute(
                    "SELECT user_id, nick_name FROM conversation_user_table WHERE conversation_id = ?", (group_id,)):
                if r["nick_name"]:
                    out[int(r["user_id"])] = r["nick_name"]
        except sqlite3.Error:
            pass
        try:
            for r in con.execute(
                    "SELECT userid, nickname FROM conversation_member_nickname_table WHERE room_id = ?", (group_id,)):
                if r["nickname"]:
                    out.setdefault(int(r["userid"]), r["nickname"])
        except sqlite3.Error:
            pass
        return out
    finally:
        con.close()


def load_messages(message_db: str, group_id: str, start_ts: int, end_ts: int) -> list[dict]:
    con = sqlite3.connect(message_db)
    con.row_factory = sqlite3.Row
    rows: list[dict] = []
    seen: set = set()
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in ("message_table", "kf_message_tableV1"):
            if table not in tables:
                continue
            for r in con.execute(
                    f'SELECT message_id, server_id, sequence, sender_id, content_type, send_time, flag, content '
                    f'FROM "{table}" WHERE conversation_id = ? AND send_time >= ? AND send_time < ? '
                    f'ORDER BY send_time, sequence', (group_id, start_ts, end_ts)):
                key = r["server_id"] if r["server_id"] else (r["send_time"], r["sender_id"], r["message_id"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(dict(r))
        rows.sort(key=lambda m: (m["send_time"], m["sequence"] or 0))
        return rows
    finally:
        con.close()


def export(group: str, hours: int = 48, db_dir: Optional[str] = None,
           end: Optional[datetime] = None, out_dir: Optional[str] = None) -> dict:
    db_dir = find_db_dir(db_dir)
    dbs = PlainDBs(db_dir).open()
    try:
        info = find_group(dbs.path("session.db"), group)
        users = load_users(dbs.path("user.db"))
        nicks = load_member_nicks(dbs.path("session.db"), info["id"])
        end_dt = (end or datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
        start_dt = end_dt - timedelta(hours=hours)
        raw = load_messages(dbs.path("message.db"), info["id"], int(start_dt.timestamp()), int(end_dt.timestamp()))

        messages = []
        for i, m in enumerate(raw, 1):
            label, text = parse_content(m["content_type"], m["content"])
            sender_id = int(m["sender_id"] or 0)
            messages.append({
                "id": i,
                "time": datetime.fromtimestamp(m["send_time"], TZ).isoformat(),
                "sender_username": str(sender_id),
                "sender_name": nicks.get(sender_id) or users.get(sender_id, f"成员{sender_id}"),
                "type": label,
                "content": text,
                "content_type": m["content_type"],
                "flag": m["flag"],
                "server_id": m["server_id"],
            })

        run_dir = out_dir or os.path.join(os.getcwd(), "reports",
                                          f"{datetime.now(TZ):%Y%m%d-%H%M%S}-{info['id'].replace(':', '')}")
        os.makedirs(run_dir, exist_ok=True)
        payload = {
            "group": info["name"],
            "conversation_id": info["id"],
            "start": start_dt.isoformat(),
            "end_exclusive": end_dt.isoformat(),
            "message_count": len(messages),
            "duplicates_removed": len(raw) - len(messages),
            "database_checks": [{"db": k, "quick_check": [v]} for k, v in dbs.checks.items()],
            "messages": messages,
        }
        with open(os.path.join(run_dir, "messages.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        with open(os.path.join(run_dir, "messages.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {info['name']}  {start_dt:%Y-%m-%d %H:%M} — {end_dt:%Y-%m-%d %H:%M} UTC+8\n")
            f.write(f"# {len(messages)} 条消息\n\n")
            for m in messages:
                stamp = datetime.fromisoformat(m["time"]).strftime("%m-%d %H:%M:%S")
                body = m["content"] or f"[{m['type']}]"
                f.write(f"[{m['id']:03d}] {stamp} {m['sender_name']}: {body}\n")
        return {"run_dir": run_dir, "group": info["name"], "count": len(messages),
                "start": start_dt.isoformat(), "end": end_dt.isoformat()}
    finally:
        dbs.cleanup()
