# report_spec.json 与 insights.json 完整 Schema

两种渲染器（PNG / HTML）都只消费 `report_spec.json`，不直接读消息。
要改变报告的样子，改 spec；要改变内容，改 insights 或规则。

## report_spec.json

```jsonc
{
  "version": 1,
  "meta": {
    "group": "示例项目群",
    "start": "2026-10-29T09:13:39+08:00",       // ISO8601 含时区
    "end":   "2026-10-31T09:13:39+08:00",
    "window": "10.29 09:13 — 10.31 09:13",
    "mode": "dispatch",                          // dispatch | general
    "kind_label": "派单日报 / 2026.10.31",        // 头部小字
    "title": "这 48 小时，群里派了 91 单",         // 大标题，纯文本
    "subtitle": "示例项目群",
    "lede": "…<em>可高亮</em>…",                  // 导语；HTML 支持 <em>，其余标签会被剥离
    "generated_at": "…", "message_count": 185, "sender_count": 19
  },
  "kpis": [                                      // 2–4 个，多了只取前 4
    {"label": "群消息", "value": 185, "unit": "条", "hint": "19 位发言人"},
    {"label": "加急单占比", "value": 47, "unit": "%", "hint": "43 单"}
    // unit 为 "%"/"点" 时贴在数字后；其他单位（条/单/元/人）渲染成小字
  ],
  "sections": [ /* 见下 */ ],
  "notes": ["口径条目…"],                        // 页尾"数据范围与阅读口径"编号列表
  "footer": "示例项目群 · 2026.10.31"
}
```

## sections 支持的类型

```jsonc
// 1. bars —— 横向条形（价格带、话题热度、活跃成员）
{"type":"bars", "title":"报价结构", "note":"按正文标价归档",
 "items":[{"label":"20–40 元","value":34,"display":"34 单"}],
 "highlight":"20–40 元"}                       // 可选：该条用暖色高亮

// 2. table —— 通用表格；align 可省
{"type":"table", "title":"主力派单方", "note":"Top 6",
 "columns":[{"label":"老师","align":"l"},{"label":"合计","align":"r"}],
 "rows":[["PPT沐言老师","724 元"]]}

// 3. cards —— 结论卡（每条：加粗标题 + 正文）
{"type":"cards", "title":"关键结论",
 "items":[{"title":"节奏：当天交付为主","body":"正文…，支持 <em>强调</em>"}]}

// 4. list —— 明细行（加急单/待办/文件/摘录）；tone 决定配色
{"type":"list", "title":"加急单 · 共 43 条", "tone":"warn",   // warn|ok|info
 "limit":12,                                   // PNG 最多画几条，HTML 全部展示
 "items":[{"time":"2026-10-29T09:50:08+08:00",   // ISO8601，渲染成 MM-DD HH:MM
           "who":"首席提子老师", "text":"30r 简单美化 中午要",
           "tag":"加急",                          // 可选徽标
           "value":"30 元"}]}                     // 可选右侧数值

// 5. hours —— 0–23 时柱状图，values 必须正好 24 个整数
{"type":"hours", "title":"派单时段分布", "values":[0,0,…]}

// 6. donut —— 环形占比图
{"type":"donut", "title":"消息构成", "items":[{"label":"文本","value":108}]}

// 7. text —— 纯文字板块（风险、说明）
{"type":"text", "title":"风险与待确认", "paragraphs":["第一段","第二段"]}
```

## insights.json（general 模式的语义输入）

由 AI 阅读 `digest.md` 后写出到 run 目录，`analyze.py --mode general --insights` 消费。
**所有字段可选**——缺什么就退化为纯统计板块。值里可用 `<em>…</em>` 做强调（其他标签会被剥离）。

```jsonc
{
  "title":  "这两天群里在忙什么：报价、排期与文件往来",   // 大标题（覆盖默认）
  "lede":   "48 小时里 <em>185 条消息</em>…",            // 导语段
  "kind_label": "群聊总结 / 2026.10.31",
  "kpis":   [/* 同 spec.kpis 结构；给了就整体替换默认 KPI */],

  "highlights_title": "关键结论",
  "highlights": [ {"title":"节奏：当天交付为主", "body":"依据 digest 中的消息归纳…"} ],

  "topics_title": "话题分布",                            // topics 是手工归类的话题
  "topics": [ {"label":"排期对齐","value":23} ],          // value=相关消息条数（数出来的，别估）

  "todos_title": "待办与跟进",
  "todos": [ {"who":"张三", "time":"2026-10-30T21:09:00+08:00",
              "text":"42页复刻 周日终稿", "tag":"跟单", "due":"周日"} ],

  "quotes_title": "原话摘录",
  "quotes": [ {"who":"李四", "time":"…", "text":"原文，不改写", "tag":"最大单"} ],

  "risks_title": "风险与待确认",
  "risks": ["预算口径在群里出现两种说法（[112] 与 [138]），需线下确认。"],

  "notes": ["自定义口径…"]                              // 给了就整体替换默认口径
}
```

### 写 insights 的质量要求

1. **每条结论可追溯**：能在 digest.md 里指出支撑消息（编号/时间），写不准的不写。
2. **话题 value 必须真实计数**，不要拍脑袋填。
3. **todos 保留原话关键信息**（交付物、时间、数量），due 尽量具体。
4. **risks 放分歧、改口、未决事项**，并引用消息编号。
5. 人名可用群昵称，但涉及个人隐私的报告在对外分享前应打码。
