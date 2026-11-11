"""统一消息模型。"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional, Any


@dataclass
class Message:
    id: str                     # 稳定短 ID，用于报告引用（如 m1423）
    seq: int                    # 源序（sort_seq/local_id）
    ts: int                     # 秒级时间戳（本地时区）
    sender_id: str              # 群内 wxid / 昵称 ID
    sender_name: str            # 展示名
    kind: str                   # text/image/voice/video/sticker/file/link/miniprogram/quote/system/call/redpacket/other
    text: str                   # 正文；非文本为类型标记占位
    raw_type: int = 0
    quote: Optional[dict] = None      # {"sender":..,"text":..}
    extra: dict = field(default_factory=dict)  # 链接/文件/小程序等可解析字段
    db: str = ""                # 来源库文件（分片溯源）

    def to_json(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Chat:
    account_wxid: str
    account_name: str
    chat_id: str                # 群 username，如 1234@chatroom
    chat_name: str
    members: dict = field(default_factory=dict)   # wxid -> 群内昵称
    is_group: bool = True
    data_dir: str = ""
    wechat_version: str = ""

    def display(self, sender_id: str) -> str:
        if sender_id in self.members and self.members[sender_id]:
            return self.members[sender_id]
        return sender_id


def make_msg_id(db_tag: str, local_id: int) -> str:
    """报告引用的稳定 ID：分片前缀 + 本地自增 ID。"""
    return f"{db_tag}-{local_id}"


def biz_key(m: Message) -> str:
    """去重键：同一条消息在不同分片/重放中应得到同一键。"""
    h = hashlib.sha1()
    h.update(str(m.ts).encode())
    h.update(b"\x01")
    h.update(m.sender_id.encode("utf-8", "ignore"))
    h.update(b"\x01")
    h.update(m.kind.encode())
    h.update(b"\x01")
    h.update((m.text or "")[:512].encode("utf-8", "ignore"))
    h.update(b"\x01")
    h.update(str(m.extra.get("server_id", "")).encode())
    return h.hexdigest()
