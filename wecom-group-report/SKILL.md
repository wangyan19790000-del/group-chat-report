---
name: wecom-group-report
description: 读取本机已登录企业微信（WXWork 5.x）指定群在指定时间窗内的聊天记录，自动生成 PNG 长图日报和可交互 HTML 网页报告，支持派单/接单群（规则统计）与工作群/项目群（AI 归纳）两种报告模式。当用户要求"总结企业微信群聊天记录、生成群日报/周报、把群记录做成图或网页版报告、派单日报"时使用此技能。仅限读取用户本人账号的群数据，禁止用于监控他人。
agent_created: true
---

# 企业微信群记录 → PNG / HTML 总结报告

从本机已登录的企业微信客户端数据库（加密）中**只读**抽取指定群的聊天记录，
自动产出四种交付物：`report.png`（1600px 长图）、`report.html`（可交互网页）、
`summary.md`、`report_spec.json`。全程本地处理，数据不出本机。

## 硬性前提

- **Windows** + 企业微信客户端（WXWork 5.x）**已启动并登录、保持运行**（取钥需读其进程内存）。
- Python 3.10+，依赖 `cryptography`、`pillow`。
- 仅限本人账号所在群；不得用于监控、监控式管理或绕过企业数据政策。

## 工作流

所有命令在技能的 `scripts/` 目录下执行。解释器按以下优先级选择：

1. 技能目录内 `scripts/.venv/` 或用户级 `%LOCALAPPDATA%\wecom-group-report\.venv\Scripts\python.exe`（若存在，优先用它）；
2. 当前项目已有的虚拟环境（能 `import cryptography, PIL` 的）；
3. 系统 `python`。若缺依赖，先跑一次 `python setup_env.py`（自动建 venv 并装 `cryptography`、`pillow`，国内走清华镜像）。

### Step 0 · 环境自检

```bash
python check_env.py
```

任一 FAIL 必须先解决（装依赖、启动并登录企业微信）。WARN 可继续但说明风险。

### Step 1 · 确认群名并抽取

用户给的群名可能是简称，先用 `--list` 看最近活跃群名，再抽取：

```bash
python extract.py --list --hours 48                      # 列最近 48h 活跃的群
python extract.py --group "群名或关键词" --hours 24       # 抽取并导出
# 可选：--end "2026-10-31T12:00:00+08:00"  --out <目录>
```

输出 `<run-dir>/`：`messages.json`、`messages.txt`、`facts.json`（统计）、`digest.md`（给 AI 读的紧凑全文）。
抽取约需 1–3 分钟（进程内存取钥），失败时重试即可；详见 references/wxwork-internals.md。

### Step 2 · 选择报告模式并生成规格

```bash
python analyze.py --run-dir <run-dir> --mode auto
```

- **dispatch（派单/接单群）**：正文大量出现"数字+R/元/页"时自动命中。规则统计报价结构、
  主力派单方、加急单、高价值单，**全自动，无需 AI 介入**。
- **general（工作群/项目群/客户群）**：需要语义结论。执行：

  1. 完整阅读 `<run-dir>/digest.md`（含消息编号与时间戳）；
  2. 参照 `references/report-spec.md` 中 **insights.json** 的 schema，把标题、导语、关键结论、
     待办、原话摘录等写为 `<run-dir>/insights.json`；
  3. 重新运行 `python analyze.py --run-dir <run-dir> --mode general --insights <run-dir>/insights.json`

  insights.json 不是必填——缺省时仍会产出纯统计版（活跃成员、时段、消息构成、文件链接），
  但正式交付**必须**补写语义结论，否则报告只是数字没有观点。

  写 insights 的要求：结论必须有 digest 中的消息依据，不得编造；待办注明来源消息时间；
  引用原话用原文，不改写。

### Step 3 · 双渲染

```bash
python render_png.py  --run-dir <run-dir>   # report.png（缺中文字体时 --font 指定）
python render_html.py --run-dir <run-dir>   # report.html（自包含，可发手机/他人）
```

### Step 4 · 交付

- 用文件预览工具展示 `report.png` 与 `report.html`，并给出 2–3 条关键结论摘要。
- 口径提醒必须传达：图片需求未 OCR、报价未核实成交、消息以本机同步为准。

## 模式判定速查

| 群的特征 | 模式 |
|---|---|
| 接单/派单/外包/报价 | dispatch |
| 部门群、项目群、客户群、班级群 | general |
| 拿不准 | `--mode auto`（≥25% 文本消息带报价 → dispatch） |

## 常见问题

| 症状 | 处理 |
|---|---|
| 取钥超时/失败 | 确认客户端在运行且已登录；重跑；以同一用户权限运行，不要用管理员提权 |
| 最新消息缺失 | 客户端长时间未关闭时最新消息可能在 WAL 中，先正常退出企微再重跑 |
| PNG 中文乱码/方块 | `--font C:/Windows/Fonts/msyh.ttc`（或任意中文字体） |
| 找不到群 | 用 `--list` 核对群名；群名支持部分匹配，重名时用更长的名字 |
| 大窗口很慢 | 先 `--hours 24` 试用；>168h 的窗口导出和渲染都会明显变慢 |

深入细节（数据库结构、解密算法、消息类型表、spec 完整 schema）：
- `references/wxwork-internals.md` — 解密原理、故障排查
- `references/report-spec.md` — report_spec.json 与 insights.json 的完整 schema 和示例
