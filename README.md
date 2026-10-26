# Lingering-Warmth · 余温

A memory system that lets him remember you.

AI 会失忆，是因为它只有当下、没有时间。余温是两个人一起写的一本书：一页不会改的扉页（角色是谁），再加八层会生长的记忆（和你之间发生了什么），让它从被动响应走到主动牵挂。

人走了，温度还在。

> 基于 [Ombre Brain](https://github.com/P0luz/Ombre-Brain)，并参考了 [EbbingFlow](https://github.com/mmx920/ebbingflow)。详见 [NOTICE](NOTICE)。

## 给谁用

想让自己的角色记住彼此的人，比如乙游玩家、自己搭前端接 DeepSeek 等 API 的人。不需要会写代码也能读懂设计；想自己搭、但不懂代码的人，看 [从零开始搭余温](docs/start.md)。

## 三档

| 档 | 他会…… | 包含 |
|---|---|---|
| 第一档 | 记得你 | 扉页、随笔、续写，加上校对 |
| 第二档 | 想起来 | 翻阅、折角、褪墨 |
| 第三档 | 想你 | 修订、留白、来信 |

这些名字和旧叫法（世界书、流入、交接、OOC 守门人……）的对照，见 [design.md](docs/design.md) 的「名字对照」。

从第一档开始，慢慢往上加。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/start.md](docs/start.md) | 从零开始：不懂代码的人怎么让 AI 帮忙搭起来 |
| [docs/design.md](docs/design.md) | 设计：扉页 + 八层，每层解决什么问题、怎么做；名字对照 |
| [docs/architecture.md](docs/architecture.md) | 参考实现的架构：模块、数据流、目录结构 |
| [docs/schema.md](docs/schema.md) | SQLite 表结构，字段级 |
| [docs/interfaces.md](docs/interfaces.md) | 模块接口与 HTTP 接口 |
| [docs/prompts/](docs/prompts/) | 角色 prompt 模板、校对、随笔、续写用的 prompt |
| [docs/roadmap.md](docs/roadmap.md) | 路线与还没想清楚的事 |
| [examples/](examples/) | 示例扉页（世界书） |
| [AGENTS.md](AGENTS.md) | 给写代码的 agent 看的实作说明 |

## 你的记忆是你的

如果你用余温搭服务给别人用，请做到这三件事：

- 用户随时能完整导出自己的记忆
- 说清楚记忆存在哪里
- 停服前提前告知

## 状态

第一档（M1）及 M1.1、M1.2 参考实现已经可用，包含分层扉页、批次随笔、续写、校对、完整导出和最简网页。M2.1 加入翻阅与褪墨：旧记忆会按相关性和经过的时间进入对话；M2.1.1 起，他真的用上了才算想起，才更不容易淡去。

之后陆续加上：登录与目录（M2.2），能看、能改、能封记忆；待续（M2.3），他记得答应过的事；对话里封页与纪年（M2.4），说“忘掉吧”就封，纪念日他记得；来信（M3.1），白天他会主动先写一句，可以推到手机上。

## 翻阅：embedding 二选一

| 选择 | 配置 | 效果 |
|---|---|---|
| 不配 | `embedding.base_url` 留空（默认） | 免费按字面匹配；不多调用模型 |
| 在线平台 | 填 `embedding.base_url`、`embedding.api_key` 和 `embedding.model`（默认 `bge-m3`） | 按意思找，能找回说法不同但意思相近的记忆；写入和补算记忆、每轮查询都会调用 embedding 接口 |

在线平台只需提供 OpenAI 兼容的 `/embeddings` 接口；价格与免费额度以平台为准。进阶用户也可以把兼容地址指向本地 Ollama，这里不展开配置步骤。接口暂时失败时，会改按字面找，不影响聊天。

## 每说一句会调用几次模型

下面是通常一轮对话的调用次数；接近上下文上限时，续写还会偶尔多调用一次对话模型。

| 配置 | 对话模型 | 小模型 | 适合什么时候 |
|---|---:|---:|---|
| 全开（默认） | 1 次 | 2 次：校对 1 次、随笔 1 次 | 最在意语气和逐轮记忆 |
| 关校对（`gatekeeper.pre_send = false`） | 1 次 | 1 次：随笔 | 想省一次检查；仍可手动标记 OOC |
| 都省（再设 `inflow.every_n_turns = 3`） | 1 次 | 平均约 1/3 次：每三轮记一次随笔 | 想降低费用；续写前不足三轮也会总结 |

来信（M3.1）另算：白天他每隔 40–120 分钟看一眼要不要先写，真的看的时候调用一次对话模型，一天大约十次，最多写三封；规矩挡下的不调用。设 `wake.enabled = false` 就不花这份钱。

实际费用取决于模型价格和上下文长度；扉页的词条按关键词带入，语料只抽样，不会每轮塞入整本。

## 许可

余温用 [PolyForm Noncommercial 1.0.0](LICENSE)：个人自己用、改、分享给朋友，都可以，免费；不能拿去做商业用途，比如卖搭建服务、打包成付费教程或付费产品。想商用的，先联系作者。借鉴的 Ombre Brain（MIT）和 EbbingFlow（Apache-2.0）的版权声明保留在 [NOTICE](NOTICE)。

## 运行参考实现

只想先看页面、试试记忆怎么写进目录，可以在仓库目录运行 `uv run yuwen-demo`，再打开终端显示的地址，用本次生成的密码登录。试着说“我喜欢茉莉花茶”，然后点“目录”看随笔；说“我生日是 3 月 14 日”后可在“纪年”看到日期；说“这个忘掉吧”可看到封页。演示版不需要 API Key，使用示例角色；回复和随笔判断由固定规则模拟，真实模型的语气和判断效果要按下面的步骤配置 API 后才能体验。演示数据保存在被 Git 忽略的 `data/demo.db`，来信和校对在演示版中关闭。它只监听本机 `127.0.0.1`。

需要 Python 3.11+ 和一个 OpenAI 兼容 API。配置与数据只保存在本机，不要把真实 API Key、扉页（世界书）或数据库提交到仓库。

```bash
uv sync --extra test
cp config.example.toml config.toml
cp -r examples/worldbook worldbook
```

编辑 `config.toml` 中的 `base_url`、`api_key` 和模型名，并在 `[auth]` 里把 `password` 改成自己的密码（至少 8 位），然后启动。放在 HTTPS 后面时，把 `auth.secure_cookie` 设为 `true`：

```bash
uv run yuwen
```

打开 `http://127.0.0.1:8787` 聊天。完整测试使用假的模型响应，不会调用真实 API：

```bash
uv run pytest
```
