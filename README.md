# Lingering-Warmth · 余温

A memory system that lets him remember you.

AI 会失忆，是因为它只有当下、没有时间。余温给 AI 一块不会动的地基（角色是谁），再加八层会生长的记忆（和你之间发生了什么），让它从被动响应走到主动牵挂。

人走了，温度还在。

> 基于 [Ombre Brain](https://github.com/P0luz/Ombre-Brain)，并参考了 [EbbingFlow](https://github.com/mmx920/ebbingflow)。详见 [NOTICE](NOTICE)。

## 给谁用

想让自己的角色记住彼此的人，比如乙游玩家、自己搭前端接 DeepSeek 等 API 的人。不需要会写代码也能读懂设计；想自己搭的人，可以从第一档开始。

## 三档

| 档 | 他会…… | 包含 |
|---|---|---|
| 第一档 | 记得你 | 世界书、流入、交接，加上 OOC 守门人 |
| 第二档 | 想起来 | 浮现、日记、遗忘 |
| 第三档 | 想你 | 重铸、梦境、唤醒 |

从第一档开始，慢慢往上加。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 设计：地基 + 八层，每层解决什么问题、怎么做 |
| [docs/architecture.md](docs/architecture.md) | 参考实现的架构：模块、数据流、目录结构 |
| [docs/schema.md](docs/schema.md) | SQLite 表结构，字段级 |
| [docs/interfaces.md](docs/interfaces.md) | 模块接口与 HTTP 接口 |
| [docs/prompts/](docs/prompts/) | 角色 prompt 模板、OOC 守门人、流入、交接用的 prompt |
| [docs/roadmap.md](docs/roadmap.md) | 路线与还没想清楚的事 |
| [examples/](examples/) | 示例世界书 |
| [AGENTS.md](AGENTS.md) | 给写代码的 agent 看的实作说明 |

## 你的记忆是你的

如果你用余温搭服务给别人用，请做到这三件事：

- 用户随时能完整导出自己的记忆
- 说清楚记忆存在哪里
- 停服前提前告知

## 状态

第一档（M1）参考实现已经可用，包含世界书、流入、交接、OOC 守门人、完整导出和最简网页。

## 运行参考实现

需要 Python 3.11+ 和一个 OpenAI 兼容 API。配置与数据只保存在本机，不要把真实 API Key、世界书或数据库提交到仓库。

```bash
uv sync --extra test
cp config.example.toml config.toml
cp -r examples/worldbook worldbook
```

编辑 `config.toml` 中的 `base_url`、`api_key` 和模型名，然后启动：

```bash
uv run yuwen
```

打开 `http://127.0.0.1:8787` 聊天。完整测试使用假的模型响应，不会调用真实 API：

```bash
uv run pytest
```
