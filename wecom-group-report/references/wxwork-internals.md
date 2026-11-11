# 企业微信（WXWork 5.x）本地数据库读取原理与故障排查

适用：Windows 桌面版企业微信 5.x（实测 5.0.13）。个人微信（SQLCipher 4）**不适用**本管线。

## 数据位置

- 数据目录：`<某盘>:\WXWork\<数字ID>\Data\`（`find_db_dir()` 自动探测各盘常见路径；
  也可 `--db-dir` 显式指定）。关键文件：`message.db`（消息）、`session.db`（会话/群昵称）、
  `user.db`（用户）。
- 群会话 ID 形如 `R:53590746638652`，存在 `session.db` 的 `conversation_table`
  （字段 `id / name / roomname_remark / last_message_time`）。

## 加密与取钥

- wxSQLite3 AES-128-CBC 分页加密（4096B/页，无 HMAC/reserve，区别于 SQLCipher）：
  - 页密钥 = `MD5(raw_key + LE32(page_no) + "sAlT")`
  - 页 IV  = sqlite3mc LCG 伪随机序列的 MD5
  - 第 1 页 offset 16..23 保留明文头片段，用于识别加密格式并校验候选密钥
- raw key（16B）只存在于 WXWork.exe 进程内存。`keyfind.py` 用只读 API
  （OpenProcess + ReadProcessMemory，无注入）扫描两种布局：
  1. SQL 字面量 `x'<32 hex>'`；
  2. 加密对象堆指针链。
  每个候选都要先解出合法 SQLite 页头（`SQLite format 3\x00`）才采纳。
- 解密结果写入 `%TEMP%\wxwork_plain_<pid>`，进程退出即删（atexit 兜底）。
  密钥本身**不落盘**。

## 消息正文格式

- `message.db.message_table`：`conversation_id / sender_id / content_type / send_time / content`。
- 正文是 protobuf：文本为 outer.f1 → inner.f2 → sub.f1 = UTF-8 文本；
  @消息 的 @列表在后续字段，转发记录内含控制字符（`clean_text()` 已处理）。
- 常见 content_type：2=文本、0=含@文本、3=图片、6=文件、15=表情、36=小程序/引用类、
  1002/1011=系统消息。解析失败时显示 `类型N`，不会中断。
- 发送人名字优先取 `session.db.conversation_member_nickname_table`（群昵称），
  回退 `user.db.user_table`。

## 命令行

```bash
python extract.py --list --hours 48                 # 最近活跃群列表
python extract.py --group "群名" --hours 24 --out <dir>
python analyze.py  --run-dir <dir> --mode auto [--insights insights.json] [--price-threshold 90]
python render_png.py  --run-dir <dir> [--font msyh.ttc]
python render_html.py --run-dir <dir>
```

`extract` 的耗时主要在内存扫描（1–3 分钟），与消息量无关；扫描失败直接重跑通常即可。

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 找不到密钥 / verify 全失败 | 客户端未登录或刚重启未完成同步——打开企业微信并进入一次聊天列表再跑；确认非管理员提权运行（权限差异会取不到同一进程） |
| `quick_check` 失败 | 解密参数与该版本不符，报版本号给维护者 |
| 最新消息缺几分钟 | 主库已 checkpoint 的部分在 WAL 里；本管线不读 WAL。正常退出企业微信一次再跑 |
| 群名匹配到多个 | `find_group` 取最近活跃的一个；用完整群名重跑 |
| 图片/文件乱码名 | 文件名在 protobuf 其他字段，当前版本只显示可解析部分 |
| 老版本企业微信（4.x） | 分页加密参数可能不同，未适配 |

## 合规边界

- 只读本人已登录账号的本地数据；不得读取他人账号、不得用于监控员工/家人。
- 报告对外分享前自行处理群内隐私（昵称、头像、手机号）。
- 企业数据可能受公司政策约束，输出前自行确认有权使用。
