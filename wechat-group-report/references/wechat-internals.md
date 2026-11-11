# 个人微信群记录报告 · 技术内幕与排查手册

面向维护者：解释数据库布局、解密参数、密钥获取原理，以及微信版本升级后如何重新适配。
使用层面的操作见 `SKILL.md`。

## 1. 数据目录与库结构（Windows 微信 4.x 实测）

```
<数据根>/xwechat_files/wxid_<id>_<后缀>/        ← 每个登录过的账号一个目录
    db_storage/message/message_0.db             ← 全部聊天消息（可有多分片 message_1.db…）
    db_storage/contact/contact.db               ← 联系人 / 群名 / 群成员
    db_storage/session/session.db               ← 会话列表
```

- 当前登录账号 = 消息库最近有写入的账号目录（微信运行中持续写 WAL），绝不混读多账号。
- 每个会话一张表：`Msg_<md5(username)>`；关键字段
  `local_id, local_type, create_time, real_sender_id, message_content, compress_content, sort_seq`。
- 群 username 形如 `12345678901@chatroom`；群成员昵称在 contact.db 的
  `chat_room → chatroom_member → name2id` 链上。
- 实测 `SessionTable` **没有**群昵称字段（只有 username/last_timestamp/last_sender_display_name），
  所以群名和成员昵称**只能**来自 contact.db；取不到时降级为 chatroom ID / wxid。

## 2. 加密与解密（SQLCipher 4）

实测参数（微信 4.1.15 / Windows）：

| 项 | 值 |
|---|---|
| page_size | 4096 |
| reserve | 80（页尾：IV 16B @4016 + HMAC-SHA512 64B @4032） |
| KDF | PBKDF2-HMAC-SHA512，256000 轮，salt = 库文件头 16 字节 |
| 加密 | AES-256-CBC，每页独立 IV |
| 密钥形态 | 32 字节原始密钥（raw key），解密时直接使用 |

解密实现见 `wxread/crypto.py`（纯 Python，无外部 sqlcipher 依赖）：

1. 逐页取密文（首页跳过前 16 字节 salt），用页内 IV 做 AES-CBC 解密；
2. 首页把 salt 位置写回 `SQLite format 3\x00`，每页尾部补 80 字节 0（reserve 区）；
3. `-wal` 文件按帧解析（帧头 24 字节，盐值轮换后的旧帧失效），解密后按页号覆盖合并；
4. 修正 header 偏移 28 的页数，`PRAGMA quick_check` 校验。

密钥校验：SQLCipher 首页前 16 字节是 salt（明文），所以首块解密出来的是 SQLite header
的 16..32 字节（页大小/读写版本/保留区/载荷分数），校验这些固定字段即可，
误报率 < 2^-50（`crypto.check_key`）。

## 3. 密钥获取：hook AES 密钥扩展函数（主）+ 只读内存扫描（辅）

### 3.1 实测结论（微信 4.1.15.56，2026-10-31）

**微信 4.x 不在内存里长期保留 32 字节原始密钥。** 以下四种只读内存扫描
（全部并行化、stride 16/4 全量 + stride 1 部分）均零命中：

1. 原始 32 字节形态（滑窗 + 首页 header 校验）；
2. GETU32 反序形态（OpenSSL 在小端机把 rd_key 逐 4 字节字节序反转存储）；
3. OpenSSL `AES_KEY` 结构形态（轮密钥扩展关系 w8..w11 校验，误报率 2^-128，numpy 向量化）；
4. 32 位口令字符串 + PBKDF2-HMAC-SHA512(256000) 逐候选派生。

但每次**页加解密**都会调用 AES 密钥扩展函数初始化上下文——hook 它，
从函数入参（rcx 指向的 32 字节）即可拿到流经的真实密钥（`wxread/hookfind.py`）。
这与开发期项目的结论一致：最终交付用的就是「hook AES 密钥扩展」。

### 3.2 为什么动态定位 RVA（不写死地址）

密钥扩展函数地址随微信小版本变化（4.1.15.13 → RVA 0x5936ee0；4.1.15.56 → 0x5a24720）。
`hookfind.locate_keyexpansion_rvas()` 在**宿主侧**解析 Weixin.dll 文件：

1. 手工解析 PE 节表，取 `.text` 与 `.pdata`（x64 函数边界表）；
2. 在 .text 里找 `66 0F 3A DF`（aeskeygenassist）指令的全部出现位置；
3. 按 .pdata 归属到函数，保留出现次数 ≥6 的函数（实测恰好 1 个）。

运行时按「Weixin.dll 模块基址 + RVA」用 Frida `Interceptor.attach` 挂钩，
`onEnter` 读 `args[0]` 的 32 字节发回宿主，用目标库首页校验。
只读参数、不写进程内存；密钥校验通过后只存在于本进程内存。

### 3.3 触发条件与实测耗时

hook 只在发生**页解密**时触发——微信空闲时不会触发。捕获期间用户在微信里
点开群聊、滚动聊天记录，message_0.db / session.db 密钥秒级命中（实测 1 轮 75s 内）。

**contact.db 是难点**：该库连接长期不重建，需要用户点开「通讯录」或某群的
「聊天信息」页才可能新建连接。多轮捕获（extract.py 默认最多 4 轮 × 75s）仍拿不到时，
工具会警告降级：群名显示为 chatroom ID、发言人显示为 wxid，建议改用
`--group <chatroom_id>` 抽取，或在微信里翻通讯录后重试。

### 3.4 版本适配排查（微信升级后）

1. `hookfind.locate_keyexpansion_rvas()` 返回 0 个函数 → 微信改用了非 AES-NI
   实现或换了加密库：改用 capstone 反汇编 .text 找 `aeskeygenassist` 语义等价模式，
   或检查是否还有 `Weixin.dll`（模块名可能在 JS 侧同步改）；
2. `check_key` 全部失败但能确认库是 SQLCipher → 核对 reserve/IV 布局与 HMAC 算法
   （reserve 80 = HMAC-SHA512；若换成 HMAC-SHA256 则 reserve=48，IV 偏移变为 4048）；
3. `real_sender_id` 语义、`Msg_` 表字段名变化时，改 `source.py` / `parser.py` 的映射，
   渲染层无需动。

## 4. 消息解析要点（wxread/parser.py）

- `message_content` 可能是 **zstd 压缩 BLOB**（magic `28 b5 2f fd`），需先解压；
- `local_type` 是 64 位复合值：基础类型取**低 8 位**（`t & 0xFF`），高 32 位是 appmsg 子类型；
- 常见类型：1 文本、3 图片、34 语音、43 视频、47 表情、48 位置、49 卡片（appmsg XML：
  5 链接 / 6 文件 / 19 合并转发 / 33,36 小程序 / 51 视频号 / 57 引用）、10000/10002 系统；
- 群消息正文常带 `wxid_xxx:\n` 或 `xxx@chatroom:\n` 前缀；非 wxid 形式的
  `名字:` 前缀只在名字确属已知成员时才剥，避免误删正文；
- `real_sender_id` 是**本分片 Name2Id 表的整数 rowid**（字段 `user_name`），必须用 int 键查询；
  contact.db 的 name2id 只作兜底，两者不能混用（会得到公众号等错误身份）；
- 昵称解析优先级：群成员表 > 联系人备注/昵称 > `--names` 字典（只补空缺）> wxid。

## 5. 安全与合规边界（实现层面）

- 原始库只读：全程 `open(path, "rb")`，无任何写入；hook 只读函数入参；
- 密钥只在提取进程内存存在，不写日志/配置/报告/代码仓库；
- 临时明文库仅位于 `%TEMP%\wxchat_plain_*`，注册 atexit + SIGTERM 清理；
  任务管理器强杀（TerminateProcess）会跳过清理，需手动删除；
- 本技能只能读取**当前 Windows 账号下、微信客户端已登录账号**的数据，
  技术上无法跨账号读取（多账号目录按最近写入锁定，不混读）。
