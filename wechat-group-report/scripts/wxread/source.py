"""账号定位与群聊/消息表解析（结构以 Windows 微信 4.x 实测为准）。

数据目录布局（4.x）：
    <数据根>/xwechat_files/wxid_xxxx_abcd/
        db_storage/message/message_0.db    每会话一张 Msg_<md5(username)> 表
        db_storage/contact/contact.db      联系人 / 群名 / 群成员 / name2id
        db_storage/session/session.db      会话列表（contact 不可用时的降级来源）
"""
from __future__ import annotations

import glob
import hashlib
import os
import sqlite3
import string
from typing import Dict, List


# ------------------------------------------------------------------ 定位

def find_data_root(explicit: str = "") -> str:
    """定位 xwechat_files 根目录（默认在某个盘的根目录或 我的文档 下）。"""
    if explicit:
        if os.path.isdir(explicit):
            return explicit
        raise FileNotFoundError(f"指定的数据目录不存在：{explicit}")

    roots = []
    for drive in string.ascii_uppercase:
        d = f"{drive}:/"
        if not os.path.exists(d):
            continue
        roots.append(os.path.join(d, "xwechat_files"))
        roots.append(os.path.join(d, "Users", os.environ.get("USERNAME", ""),
                                 "Documents", "xwechat_files"))
    for env in ("USERPROFILE", "HOME"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "Documents", "xwechat_files"))
            roots.append(os.path.join(base, "xwechat_files"))

    for r in roots:
        if os.path.isdir(r) and glob.glob(os.path.join(r, "*", "db_storage")):
            return os.path.normpath(r)
    raise FileNotFoundError(
        "未找到 xwechat_files 数据目录。请在微信「设置 → 文件管理」查看实际存储路径，"
        "再用 --data-root 指定（例如 --data-root \"E:/xwechat_files\"）")


def list_accounts(root: str) -> List[Dict]:
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        msg_dir = os.path.join(d, "db_storage", "message")
        if not os.path.isdir(msg_dir):
            continue
        mtime = 0.0
        for f in os.listdir(msg_dir):
            try:
                mtime = max(mtime, os.path.getmtime(os.path.join(msg_dir, f)))
            except OSError:
                pass
        name = os.path.basename(d)
        out.append({"dir": d, "wxid": name.rsplit("_", 1)[0], "mtime": mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out


def pick_account(root: str, prefer: str = "") -> Dict:
    """当前登录账号 = 最近有写入的账号目录（微信运行中持续写 WAL）。"""
    accounts = list_accounts(root)
    if not accounts:
        raise FileNotFoundError(f"{root} 下没有找到账号目录（wxid_*）")
    if prefer:
        key = prefer.strip().lower()
        for a in accounts:
            if key in os.path.basename(a["dir"]).lower() or key == a["wxid"].lower():
                return a
        names = "、".join(a["wxid"] for a in accounts[:6])
        raise FileNotFoundError(f"未找到账号 {prefer}；本机账号：{names}")
    return accounts[0]


def db_files(account_dir: str) -> Dict[str, str]:
    base = os.path.join(account_dir, "db_storage")
    out = {}
    for rel in ("message/message_0.db", "contact/contact.db", "session/session.db"):
        p = os.path.join(base, rel.replace("/", os.sep))
        if os.path.exists(p):
            out[os.path.basename(rel)] = p
    return out


# ------------------------------------------------------------------ 库结构

def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def sender_map_msgdb(conn: sqlite3.Connection) -> Dict[int, str]:
    """消息库自身的 Name2Id(rowid → user_name)。

    实测：Msg_* 表的 real_sender_id 是**本分片库内** Name2Id 的 rowid，
    不是 contact.name2id 的 rowid——用通讯录库映射会得到错误身份。
    """
    out: Dict[int, str] = {}
    if not conn:
        return out
    names = {r[0].lower(): r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    tbl = names.get("name2id")
    if not tbl:
        return out
    cols = {c.lower(): c for c in columns(conn, tbl)}
    if "user_name" not in cols:
        return out
    for rid, un in conn.execute(f'SELECT rowid, "{cols["user_name"]}" FROM "{tbl}"'):
        out[rid] = un
    return out


def display_names(contact_conn: sqlite3.Connection) -> Dict[str, str]:
    """username → 展示名（备注 > 昵称 > username）。"""
    out: Dict[str, str] = {}
    if not contact_conn or not table_exists(contact_conn, "contact"):
        return out
    cols = columns(contact_conn, "contact")
    sel = "username, nick_name" + (", remark" if "remark" in cols else "")
    for row in contact_conn.execute(f"SELECT {sel} FROM contact"):
        un, nick = row[0], row[1]
        remark = row[2] if len(row) > 2 else None
        out[un] = remark or nick or un
    return out


def list_groups(contact_conn: sqlite3.Connection) -> List[Dict]:
    out = []
    if not contact_conn or not table_exists(contact_conn, "contact"):
        return out
    cols = columns(contact_conn, "contact")
    if "remark" in cols:
        q = "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'"
    else:
        q = "SELECT username, nick_name, NULL FROM contact WHERE username LIKE '%@chatroom'"
    for un, nick, remark in contact_conn.execute(q):
        out.append({"username": un, "name": remark or nick or un, "is_group": True})
    return out


def list_groups_from_session(session_conn: sqlite3.Connection) -> List[Dict]:
    """contact.db 取不到密钥时的降级路径：从会话表推断群列表（群名可能不准）。"""
    out: List[Dict] = []
    if not session_conn or not table_exists(session_conn, "SessionTable"):
        return out
    cols = columns(session_conn, "SessionTable")
    name_col = next((c for c in ("remark", "nick_name", "nickname", "display_name")
                     if c in cols), None)
    if "username" not in cols:
        return out
    sel = f"username, {name_col}" if name_col else "username, NULL"
    for un, nm in session_conn.execute(
            f"SELECT {sel} FROM SessionTable WHERE username LIKE '%@chatroom'"):
        out.append({"username": un, "name": nm or un, "is_group": True})
    return out


def group_members(contact_conn: sqlite3.Connection, chat_id: str) -> Dict[str, str]:
    """群成员 wxid → 展示名（chat_room → chatroom_member → name2id/contact）。"""
    members: Dict[str, str] = {}
    if not contact_conn or not table_exists(contact_conn, "chat_room"):
        return members
    row = contact_conn.execute("SELECT id FROM chat_room WHERE username=?", (chat_id,)).fetchone()
    if not row:
        return members
    room_id = row[0]
    id2name = {rid: un for rid, un in
               contact_conn.execute("SELECT rowid, username FROM name2id")} \
        if table_exists(contact_conn, "name2id") else {}
    disp = display_names(contact_conn)
    if table_exists(contact_conn, "chatroom_member"):
        for (mid,) in contact_conn.execute(
                "SELECT member_id FROM chatroom_member WHERE room_id=?", (room_id,)):
            wxid = id2name.get(mid, str(mid))
            members[wxid] = disp.get(wxid, wxid)
    return members


def msg_table_for(conn: sqlite3.Connection, chat_id: str) -> str:
    """4.x 单库结构：每会话一张 Msg_<md5(username)> 表。"""
    t = f"Msg_{hashlib.md5(chat_id.encode()).hexdigest()}"
    return t if table_exists(conn, t) else ""
