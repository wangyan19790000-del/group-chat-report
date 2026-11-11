# 企业微信群记录 → PNG / HTML 日报（技能包）

把企业微信里某个群的聊天记录，一条命令变成**长图日报（PNG）**和**交互网页报告（HTML）**。
数据只在本机处理，不上传任何服务器。

> 这是给「人」看的安装说明。AI 助手读的是同目录的 `SKILL.md`。

---

## 一、安装（3 分钟）

### 1. 放进技能目录

把本文件夹解压到：

```
C:\Users\<你的用户名>\.workbuddy\skills\wecom-group-report\
```

确保解压后路径是 `...\skills\wecom-group-report\SKILL.md`（不要多套一层文件夹）。

也可以只给单个项目用：放到 `<项目目录>\.workbuddy\skills\wecom-group-report\`。

### 2. 安装依赖

需要 **Python 3.10+**。在本文件夹的 `scripts` 目录里执行一次：

```bat
python setup_env.py
```

它会自动建一个独立虚拟环境并装好 `cryptography`、`pillow`，结束时打印出要用的解释器路径，例如：

```
C:\Users\你\AppData\Local\wecom-group-report\.venv\Scripts\python.exe
```

装不上依赖时换镜像重试：

```bat
python setup_env.py --mirror https://mirrors.aliyun.com/pypi/simple
```

不想用脚本，也可以自己来：

```bat
python -m venv D:\py\wecom
D:\py\wecom\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 自检

```bat
<上面的解释器> check_env.py
```

全部 `[OK]` 即可用。出现 `[FAIL]` 按提示处理（最常见：没登录企业微信、缺依赖）。

### 4. 重启 WorkBuddy

重开对话后，助手会自动识别到新技能。

---

## 二、使用

直接用自然语言说需求即可，例如：

- 「总结一下 **产品研发群** 最近 24 小时的聊天记录，出个日报」
- 「把 **示例项目群** 这两天的派单情况做成图」
- 「生成本周 **客户对接群** 的群记录报告，要网页版」

助手会自动执行：抽取 → 判断模式 → 生成 PNG + HTML，并把文件放给你。

### 也可以自己敲命令

```bat
cd C:\Users\<你>\.workbuddy\skills\wecom-group-report\scripts
set PY=C:\Users\<你>\AppData\Local\wecom-group-report\.venv\Scripts\python.exe

%PY% extract.py --list --hours 48                     :: 先看最近活跃的群名
%PY% extract.py --group "群名" --hours 24 --out D:\report1
%PY% analyze.py --run-dir D:\report1 --mode auto
%PY% render_png.py  --run-dir D:\report1
%PY% render_html.py --run-dir D:\report1
```

产物在 `D:\report1\`：`report.png`、`report.html`、`summary.md`、`messages.txt`。

---

## 三、两种报告模式

| 模式 | 适合的群 | 报告内容 | 是否需要 AI 参与 |
|---|---|---|---|
| `dispatch` | 派单/接单/外包群 | 报价结构、主力派单方、加急单、高价值单、时段分布 | 不需要，全自动 |
| `general` | 部门群/项目群/客户群 | 关键结论、话题、待办、原话摘录、活跃成员、文件链接 | 需要：助手读原文后补写结论 |

`--mode auto` 会自动判断：文本消息里超过 25% 带报价就按派单群处理。

general 模式建议**让助手来做**——它会把语义结论写进 `insights.json` 再出图，报告才有观点，
否则只有数字统计。

---

## 四、前提与限制

**必须有**：Windows、企业微信桌面版 5.x、**已登录并保持运行**（读密钥要访问其进程内存）。

**已知限制**（报告页脚也会写）：

- 图片里的需求不做 OCR，图片消息只计数；
- 报价是群里的文本标注，不代表成交；
- 只读本机已同步的记录，手机端未同步的消息读不到；
- 企业微信 4.x 及更早版本未适配。

**合规**：只能读取你自己账号所在的群，不得用于监控他人；对外分享前请自行处理昵称、头像等隐私。

---

## 五、文件说明

```
wecom-group-report/
├── SKILL.md                     AI 助手的工作流说明
├── README.md                    本文件（给人看）
├── scripts/
│   ├── setup_env.py             一键建环境（第一次跑）
│   ├── check_env.py             环境自检
│   ├── requirements.txt         依赖清单
│   ├── extract.py               抽取群消息 → messages.json / facts.json / digest.md
│   ├── analyze.py               生成报告规格（dispatch 规则 / general+insights）
│   ├── render_png.py            出长图 report.png
│   ├── render_html.py           出网页 report.html
│   └── wxwork_reader/           解密与读取企业微信本地库
└── references/
    ├── report-spec.md           报告规格与 insights.json 的 schema
    └── wxwork-internals.md      数据库解密原理与故障排查
```

## 六、常见问题

| 现象 | 处理 |
|---|---|
| 提示找不到密钥 / 超时 | 确认企业微信已登录并在运行；关掉重开一次再试；用同一用户身份运行，别用管理员提权 |
| 最新几条消息没有 | 先正常退出企业微信再跑（最新消息可能还在 WAL 里） |
| PNG 中文是方块 | 加 `--font C:\Windows\Fonts\msyh.ttc` |
| `ModuleNotFoundError` | 用 `setup_env.py` 输出的那个解释器，而不是系统 Python |
| 找不到群 | 先 `--list` 看群名，群名支持部分匹配 |
| 助手说没有这个技能 | 检查路径是否正好是 `.workbuddy\skills\wecom-group-report\SKILL.md`，然后重启 WorkBuddy |
