# 个人微信群记录 → PNG / HTML 日报（技能包）

把**个人微信**里某个群的聊天记录，一条命令变成**长图日报（PNG）**和**交互网页报告（HTML）**。
数据只在本机处理，不上传任何服务器。

> 这是给「人」看的安装说明。AI 助手读的是同目录的 `SKILL.md`。
> 企业微信群用另一个技能包 `wecom-group-report`，两者报告格式一致。

---

## 一、安装（3 分钟）

### 1. 放进技能目录

把本文件夹解压到：

```
C:\Users\<你的用户名>\.workbuddy\skills\wechat-group-report\
```

确保解压后路径是 `...\skills\wechat-group-report\SKILL.md`（不要多套一层文件夹）。

也可以只给单个项目用：放到 `<项目目录>\.workbuddy\skills\wechat-group-report\`。

### 2. 安装依赖

需要 **Python 3.10+**。在本文件夹的 `scripts` 目录里执行一次：

```bat
python setup_env.py
```

它会自动建独立虚拟环境并装好 `pycryptodome`、`zstandard`、`pillow`、`numpy`，
结束时打印要用的解释器路径，例如：

```
C:\Users\你\AppData\Local\wechat-group-report\.venv\Scripts\python.exe
```

装不上依赖时换镜像重试：

```bat
python setup_env.py --mirror https://mirrors.aliyun.com/pypi/simple
```

### 3. 自检（注意：要用管理员身份打开终端）

```bat
<上面的解释器> check_env.py
```

全部 `[OK]` 即可用。常见 `[FAIL]`：不是管理员权限（读微信进程内存必须提权）、
微信没启动登录、缺依赖。

### 4. 重启 WorkBuddy

重开对话后，助手会自动识别到新技能。

---

## 二、使用

直接用自然语言说需求即可，例如：

- 「总结一下 **产品研发群** 最近 24 小时的聊天记录，出个日报」
- 「把 **家族群** 这周聊的内容做成图」
- 「生成本周 **客户对接群** 的群记录报告，要网页版」

助手会自动执行：抽取 → 判断模式 → 补写结论 → 生成 PNG + HTML，并把文件放给你。

### 也可以自己敲命令

```bat
cd C:\Users\<你>\.workbuddy\skills\wechat-group-report\scripts
set PY=C:\Users\<你>\AppData\Local\wechat-group-report\.venv\Scripts\python.exe

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
| `general` | 部门群/项目群/客户群/家族群 | 关键结论、话题、待办、原话摘录、活跃成员、文件链接 | 需要：助手读原文后补写结论 |

`--mode auto` 自动判断：文本消息里超过 25% 带报价就按派单群处理。
general 模式建议**让助手来做**——它把语义结论写进 `insights.json` 再出图，
报告才有观点，否则只有数字统计。

---

## 四、前提与限制

**必须有**：Windows、微信桌面版 4.x（实测 4.1.15）、**已登录并保持运行**、
**管理员身份运行**（读取微信进程内存需要）；`frida` 依赖建议装上
（取密钥的快速通道，不装也能跑但慢很多）。

**已知限制**（报告页脚也会写）：

- 只读本机已同步的记录，手机端未同步到电脑的消息读不到；
- 图片/语音/视频/表情只做类型标记，不识别内容（语音仅引用微信自带转写文本）；
- 结论由 AI 从原文归纳，可能遗漏，重要事项请回看 `messages.txt` 原文；
- 微信 3.x 及 macOS 版未适配；微信大版本升级可能需要重新适配（见 references）。

**合规**：只能读取你自己账号所在的群，不得用于监控他人；对外分享前请自行处理
昵称、头像等隐私（家人群尤其注意）。

---

## 五、文件说明

```
wechat-group-report/
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
│   └── wxread/                  解密与读取微信本地库（crypto/keyfind/source/parser）
└── references/
    ├── report-spec.md           报告规格与 insights.json 的 schema
    └── wechat-internals.md      数据库解密原理、取钥原理与版本适配排查
```

## 六、常见问题

| 现象 | 处理 |
|---|---|
| 提示"未检测到微信客户端进程" | 启动并登录微信，保持运行 |
| OpenProcess 失败 err=5 | 用**管理员身份**重新打开终端再跑 |
| hook 挂上了但一直无命中 | 微信空闲时不会解密任何数据——**捕获期间必须在微信里点开群聊、滚动聊天记录** |
| contact.db 一直缺 | 捕获期间点开「通讯录」标签页或群的「聊天信息」页；多轮失败会自动降级（群名显示为 chatroom ID），可用群 username 指定 |
| 找不到群 / 群名不对 | 先 `--list`；通讯录库未解密时群名只有 chatroom ID，用 username 指定 |
| 提示群记录未同步 | 在手机微信打开该群翻一翻，等记录同步到电脑后重试 |
| PNG 中文是方块 | 加 `--font C:\Windows\Fonts\msyh.ttc` |
| `ModuleNotFoundError` | 用 `setup_env.py` 输出的那个解释器，而不是系统 Python |
| 助手说没有这个技能 | 检查路径是否正好是 `.workbuddy\skills\wechat-group-report\SKILL.md`，然后重启 WorkBuddy |
