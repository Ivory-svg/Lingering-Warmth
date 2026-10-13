# 接口

> 各部分的新名字（扉页、随笔、翻阅、续写、校对……）和旧名的对照，见 [design.md](design.md)「名字对照」。这份文档对着代码写，英文标识符不变；正文里的中文叫法会随代码一起换。

## HTTP 接口（第一档）

所有请求和响应都是 JSON。时间格式同 [schema.md](schema.md)。

### `POST /chat`

玩家发一条消息，拿到回复。

请求：

```json
{ "content": "今天好累" }
```

响应：

```json
{
  "message_id": 128,
  "reply": "……",
  "session_id": 7,
  "ooc_rewritten": true,
  "ooc": {
    "original": "我好想你！",
    "reason": "他从不用感叹号，也不会直接说想你",
    "voice_note_id": 12
  },
  "handoff_happened": false
}
```

- `ooc_rewritten`：发出前被校对改写过
- `ooc`：只在 `ooc_rewritten` 为 true 时出现，否则为 null。前端点开小标记时显示这里的内容，不用再发请求
- `handoff_happened`：这一轮之前刚续写到新的一卷（前端可以显示一条细细的分隔线）

### `GET /messages`

网页打开或刷新时，拿回当前窗口的原话，好把对话和小标记重新画出来。

响应：数组，按时间顺序。

```json
[
  { "message_id": 127, "role": "user", "content": "今天好累", "created_at": "..." },
  {
    "message_id": 128, "role": "assistant", "content": "……", "created_at": "...",
    "ooc": { "original": "...", "reason": "...", "rewritten": "...", "voice_note_id": 12, "source": "gatekeeper" }
  }
]
```

- `ooc`：这条回复有过勘误（校对改写或玩家标记）时才有，取最新一条；否则为 null
- `role = event` 的消息不返回
- 续写分隔线暂不需要在刷新后还原

### `POST /ooc/flag`

玩家觉得“他不会这么说”。

请求：

```json
{ "message_id": 128, "player_rewrite": null }
```

- `player_rewrite` 为空：校对分析并给出改写
- `player_rewrite` 有值：直接用玩家改的版本，校对只补写原因

响应：

```json
{
  "verdict": "ooc",
  "reason": "他不会直接说想你，会绕个弯",
  "rewritten": "……",
  "voice_note_id": 12
}
```

`verdict` 为 `in_character` 时 `rewritten` 和 `voice_note_id` 为空，并告诉玩家校对觉得这句没问题；玩家仍可传 `player_rewrite` 强制存一条。

### `GET /voice-notes` / `PATCH /voice-notes/{id}`

列出勘误；`PATCH` 只允许改 `active`。

### `POST /voice-notes/{id}/overturn`

玩家点开勘误的小标记，觉得校对看走眼了：“其实他会这么说”。

请求：

```json
{ "note": "他就是会这么直接，这是他因为你变的" }
```

`note` 可空。

做三件事：

1. 这条勘误设 `active = 0`，并写上 `overturned_at`、`overturn_note`
2. 如果是校对改写的（`source = gatekeeper`），这条回复恢复成原句：在消息 `meta` 里记 `restored`，见 [schema.md](schema.md)
3. 返回恢复后要显示的内容

响应：

```json
{ "message_id": 128, "content": "我好想你！", "restored": true }
```

- 玩家标记产生的勘误（`source = player`）被推翻时，只停用勘误，回复本来就没改过，`restored` 为 false
- 已经推翻过的再推翻，直接返回当前状态，不报错
- 推翻后小标记消失，这条回复看起来和普通回复一样

### `GET /export`

完整导出：所有表的所有行，一个 JSON 文件。扉页文件不在数据库里，不导出。`auth_sessions`（登录）不是记忆，也不导出。

```json
{
  "exported_at": "...",
  "schema_version": "4",
  "tables": { "sessions": [...], "messages": [...], "memories": [...], "...": [...] }
}
```

`embedding` 字段导出为 base64。

### `GET /health`

返回 `{"ok": true, "schema_version": "4"}`。

---

## HTTP 接口（登录与目录）

设计见 [design.md](design.md)「目录」「登录：只有你能打开这本书」。

### 登录

**默认全锁。** 用一个中间件统一拦，不在每个接口上单独加。放行的只有：`GET /login`、`POST /login`、`GET /health`。没登录时：

- 页面（`GET /`、`GET /catalog`）→ 303 跳到 `/login`
- 其他接口 → 401 `{"detail": "请先登录"}`

**启动检查**：`auth.password` 为空、还是示例里的占位文字、或者不到 8 个字符，就不启动，报错：“请在 config.toml 的 [auth] 里设置 password（至少 8 位），设好再启动。”

**防跨站**：`POST`、`PATCH` 请求必须是 `Content-Type: application/json`，否则 415。加上 cookie 的 `SameSite=Lax`，别的网站就没法替你发请求。

#### `GET /login`

登录页：一个密码框、一个按钮。已经登录的直接跳回 `/`。页面自带样式和脚本，不引用任何要登录才能拿到的文件，不然登录页自己都打不开。

#### `POST /login`

```json
{ "password": "……" }
```

- 对：`{"ok": true}`，并种下 cookie
- 错：401 `{"detail": "密码不对"}`
- 错太多次：429 `{"detail": "试错太多次了，过一会儿再来", "retry_after_seconds": 900}`，带 `Retry-After` 头。秒数是离解锁还剩多久，刚锁上时是 900

比对密码：两边都先算 sha256，再用 `hmac.compare_digest` 比，不用 `==`。

**输错限制**：按来源 IP 计数（`request.client.host`），15 分钟内错 5 次，从第 5 次起锁 15 分钟；登录成功清零。存在内存里，重启就清空，够用。放在反向代理后面时所有人是同一个 IP，等于全局计数；只有一个人用，可以接受。

**cookie**：

| 项 | 值 |
|---|---|
| 名字 | `yuwen_session` |
| 值 | `secrets.token_urlsafe(32)` 生成的随机串 |
| 属性 | `HttpOnly`、`SameSite=Lax`、`Path=/`、`Max-Age` = `auth.session_days` 天 |
| `Secure` | `auth.secure_cookie = true` 时加。放在 https 后面的应该打开 |

服务器只存 cookie 值的 sha256，不存原文，见 schema.md 的 `auth_sessions`。

**每次请求怎么验**：cookie 的 sha256 在表里、没过期、`password_fingerprint` 和现在的密码对得上，才算登录。对不上的那一行顺手删掉。`last_seen_at` 最多一小时更新一次，不用每个请求都写库。

`password_fingerprint` = `sha256("yuwen-session:" + 密码)`。换了密码，旧的指纹全对不上，以前登录过的设备就都失效了。

#### `POST /logout`

删掉这台设备的那一行，清掉 cookie。返回 `{"ok": true}`。前端发的时候也带 `Content-Type: application/json` 和 `{}`，免得被 415 拦下。

### 目录

#### `GET /catalog`

目录页。聊天页顶上加一个“目录”的入口，目录页有回聊天的入口，角落有“登出”。

#### `GET /memories`

| 参数 | 默认 | 说明 |
|---|---|---|
| `filter` | `all` | `all` 没封页的全部 / `unclear` 记不太清的 / `fading` 快褪掉的 / `sealed` 封页的 |
| `q` | 空 | 搜一个词：在 `summary`、`quote`、`emotion_text` 里找，按字面包含 |
| `offset` | 0 | |
| `limit` | 50 | 最多 200 |

- 只列 `kind` 为 `turn`、`handoff` 的。梦在留白的合集里，不进目录
- 按 `happened_at` 从新到旧，同一时间按 id 从大到小
- `unclear`：没封页，且 `certainty < surfacing.clear_threshold`
- `fading`：没封页，且还记得多少 < 0.2

响应：

```json
{
  "total": 132,
  "items": [
    {
      "id": 41,
      "kind": "turn",
      "happened_at": "2026-10-03T20:15:00+08:00",
      "summary": "阿栀说加班很累，沈砚让阿栀先去吃饭",
      "quote": "今天好累",
      "emotion_text": "疲惫，被关心后放松了一点",
      "certainty": 0.9,
      "clear": true,
      "retention": 0.46,
      "recall_count": 2,
      "last_recalled_at": "2026-10-08T21:02:00+08:00",
      "sealed": false,
      "edited": false
    }
  ]
}
```

- `clear`：`certainty ≥ surfacing.clear_threshold`，和翻阅递给他时标“记得清楚”的是同一个判断
- `retention`：还记得多少，0–1，两位小数。**和翻阅用同一个函数算**，不要另写一份公式，免得两边对不上
- `edited`：`memory_history` 里有这条的记录
- `embedding` 不返回

#### `GET /memories/{id}`

一条记忆的全部，加上原话和历史。`kind` 不是 `turn`、`handoff` 的返回 404。

```json
{
  "id": 41,
  "...": "同上面 items 里的字段",
  "sources": [
    { "message_id": 127, "role": "user", "content": "今天好累", "created_at": "..." },
    { "message_id": 128, "role": "assistant", "content": "……", "created_at": "..." }
  ],
  "history": [
    {
      "changed_at": "...",
      "reason": "玩家改正：不是加班，是考试",
      "snapshot": { "summary": "…", "quote": "…", "emotion_text": "…", "certainty": 0.6, "sealed": false }
    }
  ]
}
```

- `sources`：按 `source_message_ids` 取原话，内容用和 `GET /messages` 一样的规则（推翻过的显示原句）
- `history`：从新到旧

#### `PATCH /memories/{id}`

玩家改正记忆。

```json
{ "summary": "阿栀说考试很累，沈砚让阿栀先去吃饭", "quote": null, "emotion_text": null, "reason": "不是加班，是考试" }
```

- `summary`、`quote`、`emotion_text` 至少给一个；不给或给 null 的不改。`quote` 给空字符串表示清空
- 长度：`summary` 1–300 字，`quote` 0–200 字，`emotion_text` 1–100 字。超了 422
- 和现在一模一样的，不写历史，直接返回
- 改之前先把整行（不含 `embedding`）存进 `memory_history`，`reason` 写 `玩家改正：{reason}`，没填就写 `玩家改正`
- 改完：`certainty` 设为 1.0（你亲手改的就是确定的）；`embedding`、`embedding_model` 清空，后台重算一次。没配 embedding 就只清空
- 响应同 `GET /memories/{id}`

#### `POST /memories/{id}/seal`

封页或撕开。

```json
{ "sealed": true }
```

- 和现在状态一样的，什么都不做，直接返回
- 否则先存一条 `memory_history`，`reason` 写 `封页` 或 `撕开`，再改 `sealed`
- 响应：`{"id": 41, "sealed": true}`

**目录里没有删除。** 任何接口都不删 `memories` 的行。

---

## 模块接口

只描述职责和输入输出，具体函数名可由实作者决定，但要保持这些边界。

### llm

| 能力 | 输入 | 输出 |
|---|---|---|
| 对话 | messages 列表、是否用小模型、可选 `response_format=json` | 文本 |
| 估算 token | 文本 | 整数。先用 `字符数 ÷ 1.5` 粗估，中文够用 |
| embedding（第二档） | 一批文本 | 每条一个 float 数组 |

- 失败重试 2 次，指数退避。
- embedding 用**单独的配置**（`embedding.*`），不和对话模型共用地址和 key。请求格式是 OpenAI 兼容的 `POST {base_url}/embeddings`，body 为 `{"model": ..., "input": [...]}`，取 `data[i].embedding`
- 没配 `embedding.base_url` 时，embedding 能力视为关闭，不报错
- 要求 JSON 输出时，解析失败重试一次；还失败就走各模块写的降级路径，不让一轮对话崩掉。

### worldbook

扉页目录结构（见 design.md「扉页拆成三份，按需带」）：

```
worldbook/
├── core.md          # 核心，每轮都带
├── corpus.md        # 语料，一行一句，空行和 # 开头的行忽略
├── daily.md         # 日常作息（可选，第三档用）
└── entries/         # 词条，一件事一个 .md
    └── 父亲.md
```

词条文件开头用 front matter 写关键词：

```markdown
---
keys: 爸爸, 父亲, 钟表店
priority: 5
---
他父亲是钟表匠，在他十九岁时去世……
```

- `keys`：逗号分隔，中英文逗号都认。必填，没写 keys 的词条不会被带入，并在启动日志里提醒
- `priority`：整数，默认 0，越大越先带
- 不用引入 YAML 库，只解析这两个字段
- 启动时如果某个词条正文超过 `worldbook.entry_budget`，在日志里提醒玩家拆短；这个词条永远不会被带入

| 能力 | 输入 | 输出 |
|---|---|---|
| 核心 | — | `core.md` 全文。文件不存在时报错，启动失败 |
| 匹配词条 | 最近 `worldbook.scan_depth` 条原话（默认 4，玩家和他的都算） | 命中的词条，按 priority 从高到低；总字数不超过 `worldbook.entry_budget`（默认 3000）。放不下的词条跳过，继续尝试较短的低优先级词条 |
| 抽语料 | — | 随机 `worldbook.corpus_sample` 句（默认 8）；语料不够就全给 |
| 缓存 | 文件修改时间变了才重读 | |

匹配规则：关键词在原话里出现就算命中，大小写不敏感，不分词。

没有写入能力。

**旧格式兼容**：如果 `worldbook/` 下没有 `core.md`，但有 `character.prompt_file` 指向的文件（例如旧的 `character.md`），就把它当扉页核心；`worldbook/` 根目录下其他没有 front matter 的 `.md` 每轮都带，并在启动日志里建议拆成词条。

### context

输入：当前 session。输出：发给模型的 messages 列表。拼接顺序：

1. system：扉页核心
2. system：命中的词条（没有就跳过），每条前面标上词条名
3. system：抽到的语料，前面写一句“参考这些句子的感觉，用他的方式说新的话，不要原句复述”（没有语料就跳过）
4. system：勘误（最近 K 条 active 的），格式“原来想说：…… / 他会说：…… / 因为：……”
5. system：续写摘要（如果本 session 接续了上一卷）
6. 上一卷最后 N 条原话（如果发生过续写）
7. 本 session 的所有原话
8. 第二档起：翻阅的记忆插在 5 和 6 之间

匹配词条用的“最近几句”，包含玩家刚发的这一句。

### inflow（随笔，原名流入）

| 输入 | 这一轮的玩家原话 + AI 回复（及其 message id） |
|---|---|
| 做什么 | 用 [prompts/inflow.md](prompts/inflow.md) 调小模型，得到 JSON，写一条 `kind=turn` 的记忆 |
| 降级 | JSON 解析失败时：summary 存原话前 100 字，emotion_text 存“未标注”，certainty 0.5 |
| 时机 | 回复发出后在后台跑，不阻塞玩家 |
| 攒几轮 | `inflow.every_n_turns`（默认 1）。设成 N 时，每攒满 N 轮才调一次小模型，把这 N 轮记成一条随笔，`source_message_ids` 包含这 N 轮的所有原话。续写前没攒满的也要先总结掉，不能丢 |
| 用上了哪些（第二档） | 这批轮次的回复里，`meta.surfaced_memory_ids` 有东西的，把这些记忆一起列给小模型，让它在 `used_memory_ids` 里返回回复真的用上的。见下 |

小模型返回格式：

```json
{
  "summary": "阿栀说今天加班很累，沈砚让阿栀先去吃饭",
  "quote": "今天好累",
  "emotion_text": "疲惫，被关心后放松了一点",
  "valence": 0.4,
  "arousal": 0.3,
  "certainty": 0.9,
  "used_memory_ids": [12]
}
```

**`used_memory_ids` 怎么用**

- 只认这批轮次里真的递过的 id，小模型返回别的数字一律忽略
- 用上的那条：`recall_count + 1`，`last_recalled_at` 设为递给它的那条回复的时间（不是随笔处理的时间）。同一批里递过好几次的，只加一次，时间取最后那条回复
- 这批里没递过任何记忆时，prompt 里不加那一段，也不读这个字段

**降级**：字段缺失、不是数组、JSON 整个解析失败、走了“原话前 100 字”的降级路径，都当作一条也没用上，不加次数。宁可少记一次，不让错的越记越牢。

### handoff（续写，原名交接）

| 能力 | 说明 |
|---|---|
| 是否需要续写 | 拼好的上下文 token 估算 ≥ `context_limit × trigger_ratio`，**并且**本 session 里玩家已经发了至少 `handoff.min_turns`（默认 20）句。第一卷不受这个限制 |
| 写续写摘要 | 用 [prompts/handoff.md](prompts/handoff.md) 让**对话模型本身**（不是小模型）写，因为语气要是他的 |
| 落库 | 存 handoffs；写一条 `kind=handoff` 的记忆；关闭旧 session；开新 session |
| 带入 | 由 context 负责，见上 |

续写模型返回格式：

```json
{
  "doing": "陪阿栀等外卖，顺便聊她明天的面试",
  "where_we_are": "阿栀刚说有点紧张，我说要帮她模拟一遍",
  "emotion": "有点心疼，也想让阿栀放松下来"
}
```

### gatekeeper（校对，原名 OOC 守门人）

| 输入 | 一句回复 + 扉页核心和本轮命中的词条（不带语料）+ 修订中的变化（第一档为空）+ 最近几句上下文 |
|---|---|
| 输出 | verdict、reason、rewritten |
| 用小模型 | 是 |
| 降级 | 解析失败或超时，视为 `in_character`，原样发出。校对坏了不能让他说不出话 |

返回格式：

```json
{
  "verdict": "ooc",
  "reason": "他从不用感叹号，也不会直接说想你",
  "rewritten": "……"
}
```

规则（写在 [prompts/ooc-gatekeeper.md](prompts/ooc-gatekeeper.md) 里）：

- 只判断像不像他，不判断好不好
- 修订中的变化不算 OOC
- 拿不准时判 `in_character`

### embedder（第二档）

给记忆算向量，存进 `memories.embedding`（float32 小端字节），同时写 `embedding_model`。

| 时机 | 做什么 |
|---|---|
| 写记忆时 | 随笔、续写写完记忆后，在同一个后台任务里给它算向量。失败就留空，不影响写记忆 |
| 启动时 | 后台补算：`embedding` 为空，或 `embedding_model` 和当前配置不同（换了模型）的记忆，按 `embedding.batch_size`（默认 32）一批批补 |
| 算什么 | `summary`，加上 `quote`（有的话）。不算情绪文字 |

没配 embedding 时，什么都不做。

### surfacing（翻阅，原名浮现，第二档）

输入：当前 session、玩家刚发的这句。输出：要递给他的几条记忆（可能为空）。

**1. 查询文本**：玩家刚发的这句 + 他上一条回复，共 `surfacing.query_depth` 条（默认 2）。

**2. 候选**：`memories` 里满足以下全部条件的
- `sealed = 0`
- `kind` 不是 `dream`
- 来源原话（`source_message_ids`）不在当前上下文里：本 session 的原话、续写带进来的原话都算“在眼前”
- 按意思找时：`embedding` 不为空，且 `embedding_model` 等于当前配置

**3. 相似度**
- **配了 embedding**：查询文本算一次向量，和候选做余弦相似度（numpy，一次矩阵乘法）
- **没配 embedding**：退回字面匹配。把查询文本和记忆的 `summary + quote` 都切成相邻两个字的组合（bigram，去掉标点空白），相似度 = 交集大小 / 查询的 bigram 数。不分词，不引入新依赖

**4. 还记得多少（褪墨）**，现算，不存：

```
天数     = 现在 − max(happened_at, last_recalled_at)，单位天
稳定度   = decay.base_days × (1 + recall_count) × (1 + arousal)    arousal 为空按 0.3
保留     = exp(−天数 / 稳定度)
```

`decay.base_days` 默认 7。被想起一次，稳定度翻倍；情绪越强，忘得越慢。

**5. 排序与取舍**
- 分数 = 相似度 × (0.6 + 0.4 × 保留)。淡掉的记忆分数最多打六折，不会彻底找不到
- 相似度低于 `surfacing.min_similarity`（embedding 默认 0.45，字面匹配默认 0.2）的丢掉
- 取前 `surfacing.top_k` 条（默认 4），总字数不超过 `surfacing.max_chars`（默认 1500）

**6. 被想起**：递给他不等于想起，用上了才算。

- 翻阅时不动 `recall_count` 和 `last_recalled_at`
- 这一轮真正递进上下文的那几条，id 记在这条回复的 `messages.meta.surfaced_memory_ids` 里。发生续写的那一轮，只记续写后重新找的那一批：续写前那批没有递出去
- 随笔记到这一轮时，由小模型判断回复里用上了哪几条，只给用上的加次数，见 inflow

**7. 递给他的格式**：一条 system，插在续写摘要和上一卷原话之间（见 context 第 8 条）。

```
这些是你此刻自然想起来的往事。不要复述，不要列出来，用得上就自然带进对话；
标着“记不太清”的，要说“好像是”“我记得大概是”，不能说得很肯定。

· 10月3日（记得清楚）：阿栀说加班很累，沈砚让阿栀先去吃饭。当时的心情：疲惫，被关心后放松了一点。原话「今天好累」
· 8月11日（记不太清）：……
```

- `certainty ≥ surfacing.clear_threshold`（默认 0.7）标“记得清楚”，否则标“记不太清”
- 日期用 `happened_at`，只写月日；不是今年的写年份
- 摘要原样放，不做任何替换。写随笔摘要时就已经用名字称呼两个人（见 prompts/inflow.md），不会出现指代不清的“他”“她”
- 旧记忆如果还有“他”“她”，也原样放，不要猜着替换：替错了比不替更糟

**降级**：embedding 请求失败、numpy 出错，就退回字面匹配；字面匹配也出错，这一轮不翻阅。翻阅坏了不能让他说不出话。

### export

读所有表，按上面格式输出。不做任何过滤，封页的也导出（那是玩家自己的东西）。

**Markdown 导出**（`GET /export?format=markdown`）：给人读的版本，一个 `.md` 文件。

```markdown
# 余温 · 全书导出 · 2026-10-07

## 对话

### 第 7 卷 · 2026-10-07 20:15 起
**你** 20:15
今天好累

**他** 20:15
……吃饭了没。

—— 续写 · 新的一卷 ——

## 随笔
- 2026-10-07 20:15 · 疲惫，被关心后放松了一点 · 阿栀说今天加班很累，沈砚让阿栀先去吃饭（确定度 0.9，被想起 2 次）

## 续写
## 勘误
```

- 对话里的说话人：玩家一律写“你”，角色写 `character.name`。这是给人读的，所以玩家就是“你”
- 随笔、续写里的摘要原样放（里面用的是 `character.player_name`）
- 被推翻（`restored`）的回复显示原句
- 不导出 embedding
- 网页的“导出全书”给两个选项：给人看的（Markdown）、给程序搬家的（JSON）

---

## 预留接口（不在第一档实作，只是先占位）

| 接口 | 用途 | 档 |
|---|---|---|
| `POST /events` | 感应事件（戳一下、摸头），写成 `role=event` 的消息 | 二 |
| `GET /diary?month=2026-10` / `POST /diary/{id}/reply` | 折角日历与回复 | 二 |
| 对话里说“这个忘掉吧” | 认出来以后，走和 `POST /memories/{id}/seal` 同一套封页逻辑 | 二 |
| `GET /wake/pending` | 前端轮询来信消息（或改成 WebSocket 推送） | 三 |
| 来信事件对外通知 | 他主动说了一句话时，把内容、原因和“日常”里此刻在做什么推给外部程序（webhook），给声音、形象、报备那个项目用 | 三 |
