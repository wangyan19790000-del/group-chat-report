---
name: wechat-group-report
description: 读取本机已登录个人微信（Windows 微信 4.x）指定群在指定时间窗内的聊天记录，自动生成 PNG 长图日报和可交互 HTML 网页报告，支持派单/接单群（规则统计）与工作群/项目群/家族群（AI 归纳）两种报告模式。当用户要求"总结微信群聊天记录、生成群日报/周报、把群记录做成图或网页版报告"且对象是个人微信（非企业微信）时使用此技能。仅限读取用户本人账号的群数据，禁止用于监控他人。
agent_created: true
---

# 个人微信群记录 → PNG / HTML 总结报告

从本机已登录的微信客户端数据库（SQLCipher 4 加密）中**只读**抽取指定群的聊天记录，
自动产出：`report.png`（1600px 长图）、`report.html`（可交互网页）、`summary.md`、
`messages.json / facts.json / digest.md`。全程本地处理，数据不出本机。

与 `wecom-group-report`（企业微信版）共用同一套分析与渲染脚本，仅读取层不同。

## 硬性前提

- **Windows** + 微信客户端（Weixin 4.x，实测 4.1.15）**已启动并登录、保持运行**
  （取密钥必须读运行中微信进程的内存；微信未运行时无法工作）。
- **以管理员身份运行**（读取进程内存需要；否则 OpenProcess 失败）。
- Python 3.10+，依赖 `pycryptodome`、`zstandard`、`pillow`、`numpy`；`frida` 强烈建议
  （装了走快速 hook 取钥，不装退化为慢速内存扫描）。
- 仅限本人账号所在群；不得用于监控他人或绕过他人隐私预期。

## 工作流

所有命令在技能的 `scripts/` 目录下执行。解释器优先级：

1. `%LOCALAPPDATA%\wechat-group-report\.venv\Scripts\python.exe`（`setup_env.py` 创建的，若存在优先用）；
2. 项目里已装好依赖的 venv（能 `import Crypto, zstandard, PIL, numpy`）;
3. 系统 `python`。缺依赖时先跑一次 `python setup_env.py`（自动建 venv 装依赖，国内走清华镜像）。

### Step 0 · 环境自检

```bash
python check_env.py
```

任一 FAIL 必须先解决（装依赖、管理员身份、启动并登录微信）。

### Step 1 · 确认群名并抽取

```bash
python extract.py --list --hours 48                # 列最近 48h 活跃的群
python extract.py --group "群名或关键词" --hours 24  # 抽取并导出
# 可选：--end "2026-10-31T12:00:00"  --out <目录>  --account <wxid>
```

- 时间窗语义 `[start, end)`，含起点不含终点；`--end` 默认当前时间。
- 输出 `<run-dir>/`：`messages.json`、`messages.txt`、`facts.json`、`digest.md`。
- **取钥策略（两级）**：① Frida hook AES 密钥扩展函数（快，秒级命中，需 frida 依赖）；
  ② 只读内存扫描兜底（慢）。**捕获期间用户必须在微信里操作**（点开群聊、滚动聊天记录），
  否则微信空闲不会触发页解密、hook 一次都不会命中——extract.py 会多轮重试并打印提示。
- contact.db 密钥最难取（该库连接长期不重建）：需点开「通讯录」或群「聊天信息」页。
  多轮拿不到时工具会警告降级：群名显示为 chatroom ID、发言人显示为 wxid，
  此时用 `--group <chatroom_id>` 指定群重试。详见 references/wechat-internals.md。

### Step 2 · 选择报告模式并生成规格

```bash
python analyze.py --run-dir <run-dir> --mode auto
```

- **dispatch（派单/接单群）**：正文大量出现"数字+R/元/页"时自动命中，规则统计报价结构、
  主力派单方、加急单，全自动无需 AI。
- **general（工作群/项目群/客户群/家族群）**：需要语义结论：

  1. 完整阅读 `<run-dir>/digest.md`（带消息编号与时间戳）；
  2. 按 `references/report-spec.md` 的 **insights.json** schema，写出标题、导语、关键结论、
     待办、原话摘录到 `<run-dir>/insights.json`；
  3. 重跑 `python analyze.py --run-dir <run-dir> --mode general --insights <run-dir>/insights.json`。

  insights 缺省时仍产出纯统计版，但正式交付**必须**补写语义结论。
  写 insights 的要求：结论必须有 digest 消息依据，不得编造；引用原话用原文不改写；
  涉及家人/同事昵称的报告在对外分享前应提醒打码。

### Step 3 · 双渲染

```bash
python render_png.py  --run-dir <run-dir>   # report.png（缺中文字体时 --font 指定）
python render_html.py --run-dir <run-dir>   # report.html（自包含，可发手机/他人）
```

### Step 4 · 交付

- 用文件预览展示 `report.png` 与 `report.html`，并给出 2–3 条关键结论摘要。
- 口径提醒必须传达：只读本机已同步记录（手机端未同步的读不到）；图片/语音/视频
  只做类型标记不做内容识别；结论由 AI 归纳可能有遗漏，重要事项回看原文。

## 常见问题

| 症状 | 处理 |
|---|---|
| 提示"未检测到微信客户端进程" | 启动并登录微信；确认进程名是 Weixin.exe（4.x） |
| OpenProcess 失败 err=5 | 用管理员身份运行终端 |
| hook 挂上了但一直无命中 | 微信空闲不解密任何页——捕获期间必须点开群聊、滚动聊天记录 |
| 取不到 contact.db 密钥 | 该库连接长期不重建：捕获期间点开「通讯录」/群「聊天信息」；多轮失败就降级，用群 username 指定 |
| 群名显示为 chatroom ID / 发言人是 wxid | contact.db 未解密的降级表现，按上一条重试即可 |
| 找不到群 / 群名不完整 | 用 `--list` 核对；通讯录库未解密时群名只有 chatroom ID，用 username 指定 |
| 群记录未同步 | message_0.db 中无该群的 Msg_ 表：先在手机微信上打开该群翻一翻，等同步 |
| 最新消息缺失 | 数据库 WAL 已自动合并；仍缺说明手机端未同步完 |
| PNG 中文乱码 | `--font C:/Windows/Fonts/msyh.ttc` |
| 临时明文库遗留 | 进程被强杀可能遗留 `%TEMP%\wxchat_plain_*`，手动删除即可 |

深入细节（库结构、解密算法、密钥扫描原理、消息类型表、spec schema）：
- `references/wechat-internals.md` — 数据库布局、解密与取钥原理、版本适配排查
- `references/report-spec.md` — report_spec.json 与 insights.json 完整 schema
