# 参考实现：架构

这份文档描述参考实现长什么样。设计意图见 [design.md](design.md)，表结构见 [schema.md](schema.md)，接口见 [interfaces.md](interfaces.md)。

## 原则

1. **记忆搭在后端主程序里，不做成 MCP。** 流入、交接、梦境、唤醒都不是由模型调用触发的，而是由程序在对话前后、定时任务里触发。玩家多数接的是 DeepSeek 等 API，不一定支持工具调用。
2. **只依赖 SQLite。** 一个文件就是全部记忆，方便导出、备份、搬家。
3. **模型接口用 OpenAI 兼容格式。** DeepSeek、通义、Kimi、OpenAI 等都能接，只改 `base_url`、`api_key`、`model`。
4. **地基只读。** 程序任何地方都不写世界书。
5. **只增不删。** 记忆改写存历史，“忘掉”是封存。唯一的真删除是玩家主动要求的导出后清空。
6. **仓库里不放任何人的私人内容。** 世界书、记忆、数据库都只放示例。

## 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 玩家最容易看懂、最容易改 |
| Web 框架 | FastAPI | 自带接口文档页，前端好对接 |
| 数据库 | SQLite（标准库 `sqlite3`） | 零部署 |
| HTTP 客户端 | httpx | 调模型 API |
| 配置 | 一个 `config.toml` | 玩家只改这一个文件 |
| 向量检索（第二档） | 先用 numpy 暴力算余弦相似度 | 个人记忆量小，几万条以内够用；以后再换 sqlite-vec |

## 目录结构

```
lingering-warmth/
├── config.example.toml        # 配置示例，玩家复制成 config.toml
├── worldbook/                 # 玩家自己的世界书（.gitignore，不进仓库）
├── examples/
│   └── worldbook/             # 示例世界书：core.md、corpus.md、daily.md、entries/
├── data/                      # SQLite 数据库（.gitignore）
├── src/yuwen/
│   ├── config.py              # 读 config.toml
│   ├── llm.py                 # OpenAI 兼容的对话 / embedding 客户端
│   ├── store.py               # 所有 SQLite 读写，其他模块不直接写 SQL
│   ├── worldbook.py           # 读世界书（只读）
│   ├── context.py             # 拼装每轮发给模型的上下文
│   ├── chat.py                # 一轮对话的总流程
│   ├── inflow.py              # 流入：每轮存一条记忆
│   ├── handoff.py             # 交接：写交接、新窗口带入
│   ├── gatekeeper.py          # OOC 守门人与语感笔记
│   ├── export.py              # 完整导出
│   ├── api.py                 # HTTP 接口
│   └── prompts/               # 运行时用的 prompt 文本（从 docs/prompts 复制）
│   # 第二档以后再加：surfacing.py diary.py decay.py keepsakes.py
│   # 第三档以后再加：recast.py dream.py wake.py anniversaries.py
└── tests/
```

## 一轮对话的流程（第一档）

```
玩家发消息
  │
  ▼
chat.handle_message
  1. store：存玩家原话（messages）
  2. handoff：估算本窗 token，超过阈值就先走「交接」，开新窗口
  3. context：拼上下文
       角色 prompt（含世界书）
       + 语感笔记（最近 K 条）
       + 交接摘要（如果本窗口是接续的）
       + 本窗口的原话
  4. llm：生成回复
  5. gatekeeper（config 打开时）：自查
       像他 → 原样发出
       不像 → 用改写版发出，存一条语感笔记
  6. store：存 AI 回复
  7. 返回回复给前端
  │
  ▼（回复发出后，后台执行，不阻塞玩家）
inflow：把这一轮（玩家原话 + 回复）总结成一条记忆，标情绪和确定度
```

## 交接的流程

```
本窗口 token 估算 ≥ 上限 × handoff.trigger_ratio
  1. 用 docs/prompts/handoff.md 让 AI 自己写交接：
     在做什么、聊到哪了、此刻的情绪
  2. store：存 handoff，关闭当前 session
  3. 交接里的情绪也写一条记忆（kind = handoff）
  4. 新建 session，记下它接续的 handoff
新窗口拼上下文时：交接摘要 + 上一窗最后 N 条原话
```

N 默认 20，可在 config 里改。

## 模块之间的依赖

```
api → chat → context → worldbook, store
           → llm
           → gatekeeper → llm, store, worldbook
           → handoff → llm, store
           → inflow → llm, store
api → export → store
```

- 只有 `store.py` 写 SQL。
- 只有 `llm.py` 发网络请求。
- `worldbook.py` 只读文件。

## 配置项（config.example.toml 里要有）

| 键 | 默认 | 说明 |
|---|---|---|
| `llm.base_url` | — | OpenAI 兼容地址 |
| `llm.api_key` | — | |
| `llm.model` | — | 对话模型 |
| `llm.small_model` | 同 `llm.model` | 流入、守门人用的小模型，省钱 |
| `llm.context_limit` | 64000 | 模型上下文上限（token） |
| `character.name` | `他` | 角色名，用在守门人、流入的 prompt 和 Markdown 导出里 |
| `character.player_name` | `你` | 他怎么称呼玩家 |
| `character.prompt_file` | `worldbook/core.md` | 世界书核心（旧配置指向 `character.md` 也认） |
| `character.worldbook_dir` | `worldbook/` | 世界书目录，结构见 interfaces.md |
| `worldbook.scan_depth` | 4 | 匹配词条时看最近几条原话 |
| `worldbook.entry_budget` | 3000 | 每轮带入词条的总字数上限 |
| `worldbook.corpus_sample` | 8 | 每轮抽几句语料 |
| `handoff.trigger_ratio` | 0.75 | 用到上限的多少开始交接 |
| `handoff.raw_tail` | 20 | 新窗口带上一窗多少条原话 |
| `handoff.min_turns` | 20 | 换窗后玩家至少说几句，才允许再交接。只是防止连续换窗的保险，正常要几百句才会换一次窗 |
| `inflow.every_n_turns` | 1 | 每几轮总结一次记忆；调大省钱 |
| `embedding.base_url` | 空 | OpenAI 兼容的 embedding 地址；留空 = 不用 embedding，浮现退回字面匹配 |
| `embedding.api_key` | 空 | |
| `embedding.model` | `bge-m3` | |
| `embedding.batch_size` | 32 | 补算时一批几条 |
| `surfacing.enabled` | true | 关掉就不浮现 |
| `surfacing.query_depth` | 2 | 拿最近几条原话去找 |
| `surfacing.top_k` | 4 | 一次最多递几条 |
| `surfacing.max_chars` | 1500 | 递过去的总字数上限 |
| `surfacing.min_similarity` | 0.45 | 按意思找时的门槛；字面匹配固定用 0.2 |
| `surfacing.clear_threshold` | 0.7 | 确定度到多少算“记得清楚” |
| `decay.base_days` | 7 | 遗忘的基础天数；越大忘得越慢 |
| `gatekeeper.pre_send` | true | 发出前是否自查 |
| `gatekeeper.voice_notes_in_context` | 10 | 拼上下文时带几条语感笔记 |
| `storage.db_path` | `data/yuwen.db` | |
| `server.host` / `server.port` | 127.0.0.1 / 8787 | |

**embedding 怎么配**（二选一，想配就配）：

| 选择 | 怎么填 | 说明 |
|---|---|---|
| 不配 | `embedding.base_url` 留空 | 浮现按字面找。不多花钱，不用多注册 |
| 在线平台 | 填平台给的地址、key，模型选 bge-m3 | 例如 SiliconFlow、OpenRouter 等提供 bge-m3 的平台。按量计费，很便宜，价格和免费额度以平台为准 |

对话模型（例如 DeepSeek）不一定提供 embedding，所以单独配置。

进阶：任何 OpenAI 兼容的 `/embeddings` 地址都能用，包括在自己电脑上用 Ollama 跑 bge-m3（`http://localhost:11434/v1`）。教程不教这一条。
