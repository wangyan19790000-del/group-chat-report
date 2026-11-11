"""消息归一化解析（字段语义以 Windows 微信 4.x 实测为准）。

要点：
  · message_content 可能是 zstd 压缩的 BLOB（magic 28 b5 2f fd），也可能是纯文本；
  · local_type 是 64 位复合值，基础类型取「低 8 位」（t & 0xFF），不是低 32 位；
  · 群消息正文常带 "wxid_xxx:\\n" 或 "xxx@chatroom:\\n" 前缀，需要剥离；
  · 少量卡片消息 local_type=1 但正文是 <appmsg> XML，按卡片解析。
"""
from __future__ import annotations

import html
import io
import re
import xml.etree.ElementTree as ET
from typing import Tuple

from .model import Message, make_msg_id

try:                        # zstd 为可选依赖：缺失时退化为纯文本解码
    import zstandard as _zstd
except Exception:           # pragma: no cover
    _zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GROUP_PREFIX_RE = re.compile(r"^(wxid_[0-9A-Za-z_-]+|[^:\r\n]+@chatroom):\s*")
BARE_PREFIX_RE = re.compile(r"^([A-Za-z0-9_\-@.]{4,64}):[ \t]*\r?\n")

KIND_BY_TYPE = {1: "text", 3: "image", 34: "voice", 43: "video", 47: "sticker",
                48: "location", 50: "call", 10000: "system", 10002: "system",
                51: "system", 100: "system"}

KIND_LABEL = {"text": "文本", "image": "图片", "voice": "语音", "video": "视频",
              "sticker": "表情", "file": "文件", "link": "链接", "miniprogram": "小程序",
              "quote": "引用", "system": "系统", "location": "位置", "call": "通话",
              "other": "其他"}

FILE_SIZE_UNITS = [(1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")]


def low_type(t) -> int:
    """64 位 local_type → 基础类型（实测 219043332145 → 49）。"""
    if isinstance(t, int) and t > 0xFFFF:
        return t & 0xFF
    return t or 0


def _fmt_size(n: int) -> str:
    for div, unit in FILE_SIZE_UNITS:
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _zstd_decompress(data: bytes) -> bytes:
    if _zstd is None:
        raise RuntimeError("未安装 zstandard，无法解压 zstd 内容（pip install zstandard）")
    with _zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
        return reader.read()


def decode_blob(value) -> str:
    """BLOB/文本 → 文本。zstd 先解压，去 NUL，再从首个 XML 根节点截起。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\x00", "").strip()
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return str(value).strip()

    data = bytes(value)
    if data.startswith(ZSTD_MAGIC):
        try:
            data = _zstd_decompress(data)
        except Exception:
            return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", "ignore")
    text = text.replace("\x00", "").strip()

    starts = [p for p in (text.find("<msg"), text.find("<appmsg"), text.find("<sysmsg"))
              if p >= 0]
    if starts:
        text = text[min(starts):]
    return text.strip()


def strip_group_prefix(text: str) -> Tuple[str, str]:
    """群文本常见形态 "wxid_xxx: 正文" → (发送者, 去前缀正文)。"""
    if not text:
        return "", text
    m = GROUP_PREFIX_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def strip_known_prefix(text: str, known) -> str:
    """剥掉 "某个已知成员名:" 前缀；不在成员名单里的不动，避免误删正文。"""
    if not text or not known:
        return text
    m = BARE_PREFIX_RE.match(text)
    if not m:
        return text
    name = m.group(1)
    if name in known or name.lower() in {k.lower() for k in known}:
        return text[m.end():]
    return text


def _xml_find_text(xml: str, path: list) -> str:
    xml = decode_blob(xml)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        tail = path[-1]
        m = re.search(rf"<{tail}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tail}>", xml, re.S)
        return html.unescape(m.group(1)).strip() if m else ""
    node = root
    for p in path:
        node = node.find(p)
        if node is None:
            return ""
    return (node.text or "").strip()


def _classify_appmsg(xml: str) -> tuple:
    """49 类卡片（链接/文件/小程序/引用/合并转发）解析。"""
    kind, text, extra, quote = "link", "", {}, None
    app_type = _xml_find_text(xml, ["appmsg", "type"])
    title = _xml_find_text(xml, ["appmsg", "title"])
    url = _xml_find_text(xml, ["appmsg", "url"])
    des = _xml_find_text(xml, ["appmsg", "des"])
    source = (_xml_find_text(xml, ["appmsg", "sourcedisplayname"])
              or _xml_find_text(xml, ["appmsg", "appinfo", "appname"]))

    if app_type == "57":                       # 引用回复
        kind = "quote"
        ref_disp = _xml_find_text(xml, ["appmsg", "refermsg", "displayname"])
        ref_raw = _xml_find_text(xml, ["appmsg", "refermsg", "content"])
        m = re.search(r"<title>(.*?)</title>", ref_raw, re.S)
        ref_content = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ref_raw
        text = title or "(引用)"
        quote = {"sender": ref_disp, "text": ref_content}
    elif app_type == "6":                      # 文件
        kind = "file"
        size = _xml_find_text(xml, ["appmsg", "appattach", "totallen"])
        text = f"[文件] {title}"
        extra.update({"file_name": title,
                      "file_size": _fmt_size(int(size)) if size.isdigit() else size})
    elif app_type in ("33", "36"):             # 小程序
        kind = "miniprogram"
        text = f"[小程序] {title}"
        extra.update({"title": title, "app": source})
    elif app_type == "19":
        kind = "link"
        text = f"[合并转发] {title}"
    elif app_type == "51":
        kind = "link"
        text = f"[视频号] {title}" if title else "[视频号]"
        extra.update({"title": title, "app": source})
    elif app_type == "5" or (url and title):
        kind = "link"
        text = title or "[链接]"
        extra.update({"title": title, "url": url, "app": source})
        if des:
            extra["des"] = des[:200]
    else:
        text = title or "[卡片消息]"
        extra.update({"title": title, "app": source})
    if url and "url" not in extra:
        extra["url"] = url
    extra = {k: v for k, v in extra.items() if v not in ("", None)}
    return kind, text, extra, quote


def classify(ltype: int, content, compressed, group: bool = False) -> tuple:
    """返回 (kind, text, extra, quote)。"""
    t = low_type(ltype)
    raw = decode_blob(content)
    if not raw and compressed not in (None, b"", ""):
        raw = decode_blob(compressed)
    if group:
        _, raw = strip_group_prefix(raw)

    if t == 1 and ("<appmsg" in raw or raw.lstrip().startswith("<msg")):
        t = 49

    if t == 49:
        kind, text, extra, quote = _classify_appmsg(raw)
    else:
        kind = KIND_BY_TYPE.get(t, "other")
        text, extra, quote = "", {}, None
        if t == 1:
            text = raw.strip()
        elif t in (3, 34, 43, 47):
            text = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]"}[t]
        elif t in (10000, 10002, 51, 100):
            text = re.sub(r"<[^>]+>", "", raw).strip() or "[系统消息]"
        elif t == 48:
            text = "[位置]"
            label = (_xml_find_text(raw, ["location", "@poiname"])
                     or _xml_find_text(raw, ["location", "@label"]))
            if label:
                text = f"[位置] {label}"
                extra["location"] = label
        elif t == 50:
            text = "[通话]"
        else:
            text = re.sub(r"<[^>]+>", "", raw).strip()

    text = _clean_text(text)
    if not text:
        text = "[文本]" if t == 1 else f"[类型{t}]"
    return kind, text, extra, quote


def _clean_text(s: str) -> str:
    """去控制字符与 HTML 实体；二进制误解码产生的乱码退化为空串。"""
    if not s:
        return ""
    s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch >= " " or ch in "\n\t")
    s = s.strip()
    if not s:
        return ""
    bad = sum(1 for ch in s if ch == "\ufffd")
    if bad * 3 > len(s):
        return ""
    return s


def normalize(db_tag: str, local_id: int, ltype: int, ctime: int, sender_id, content,
              compressed, id2name: dict, nick: dict, seq: int,
              group: bool = True) -> Message:
    sid = str(sender_id)
    if sid.isdigit():
        wxid = id2name.get(int(sid)) or id2name.get(sid) or sid
    else:
        wxid = sid
    display = nick.get(wxid) or wxid
    try:
        kind, text, extra, quote = classify(ltype, content, compressed, group=group)
    except Exception as e:            # 单条解析失败不应中断整轮提取
        kind, text = "other", f"[类型{low_type(ltype)}·解析失败]"
        extra, quote = {"error": str(e)[:120]}, None

    if group:                          # 前缀里的 wxid 可作为 real_sender_id 缺失时的兜底
        prefix, _ = strip_group_prefix(decode_blob(content))
        if prefix and (not sid.isdigit() or wxid == sid):
            wxid = prefix
            display = nick.get(wxid) or wxid

    if group:
        known = set(id2name.values()) | set(nick.keys())
        text = strip_known_prefix(text, known)

    ts = int(ctime or 0)
    ts = ts // 1000 if ts > 10 ** 12 else ts
    return Message(id=make_msg_id(db_tag, local_id), seq=seq or local_id, ts=ts,
                   sender_id=wxid, sender_name=display, kind=kind, text=text,
                   raw_type=ltype, quote=quote, extra=extra, db=db_tag)
