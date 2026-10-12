# 路线

## 项目路线

1. **开源模板仓库**（现在）：设计文档 + 第一档参考实现 + OOC 守门人
2. **收费服务**：搭建教学、角色 prompt 代写、OOC 调教、世界书整理
3. **托管 App**：有需求再做

前端的其他功能（一起看电影、听音乐、表情包等）和他的样子（声音、形象、报备）不放在这个仓库，单独开一个项目。余温这边只留唤醒事件的对外通知，让那边接得上。

## 参考实现的里程碑

### M1 · 第一档：他记得你

- [x] 配置读取、SQLite 建表与迁移
- [x] OpenAI 兼容客户端（对话 + token 粗估）
- [x] 世界书读取
- [x] 上下文拼装
- [x] `POST /chat` 跑通一轮对话
- [x] 流入：每轮后台写一条记忆
- [x] 交接：超阈值自动交接，新窗口带摘要 + 最后 N 条原话
- [x] OOC 守门人：发出前自查 + `POST /ooc/flag` + 语感笔记进上下文
- [x] `GET /export` 完整导出
- [x] 一个最简单的网页聊天界面，能演示以上功能

### M1.1 · 守门人不出声

见 [design.md](design.md)「守门人在界面上不出声」。

- [x] `POST /chat` 响应加上 `ooc`（原句、原因、语感笔记 id）
- [x] 新增 `GET /messages`，刷新后能还原对话和小标记
- [x] 网页：去掉每条回复下的“他不会这么说”按钮，改为长按 / 右键菜单
- [x] 网页：改写过或被标记过的回复，角落显示小标记，点开看原句和原因
- [x] 网页：没有 OOC 的回复不显示任何额外元素
- [x] 测试覆盖：改写时响应带 `ooc`、未改写时为 null、`/messages` 带回小标记
- [x] 迁移 002：`voice_notes` 加 `overturned_at`、`overturn_note`
- [x] 新增 `POST /voice-notes/{id}/overturn`：停用笔记、恢复原句（守门人改写的才恢复）
- [x] 拼上下文和 `GET /messages` 时，`meta.restored` 的消息改用语感笔记的 `original`
- [x] 网页：小标记点开后有“其实他会这么说”，可以留一句话，推翻后小标记消失、气泡换回原句
- [x] 测试覆盖：推翻守门人改写会恢复原句并停用笔记；推翻玩家标记只停用笔记；重复推翻不报错；`messages.content` 始终不变

### M1.2 · 世界书拆分与省钱

见 [design.md](design.md)「世界书拆成三份」「每说一句话要调几次模型」，接口见 [interfaces.md](interfaces.md) 的 worldbook、context、inflow、handoff、export。

- [x] 世界书读取改成 核心 / 词条 / 语料 三份；词条解析 `keys`、`priority`；保留旧格式兼容
- [x] 拼上下文：核心 + 命中词条 + 抽到的语料；守门人只看核心和命中词条
- [x] 配置：`character.name`、`character.player_name`、`worldbook.*`、`handoff.min_turns`、`inflow.every_n_turns`
- [x] 守门人、流入的 prompt 用 `character.name` 和 `character.player_name`，不再写死“这个角色”“玩家”
- [x] 交接：换窗后至少 `handoff.min_turns` 句才允许再换
- [x] 流入：支持每 N 轮总结一次；交接前没攒满的先总结掉
- [x] `GET /export?format=markdown`；网页导出给两个选项
- [x] README 加一张“每说一句调几次模型”的对照表
- [x] 测试覆盖：关键词命中 / 不命中、超出 entry_budget 时按 priority 取舍、语料抽样数量、旧格式兼容、换窗后 min_turns 内不会再交接、every_n_turns=3 时三轮才写一条记忆、Markdown 导出内容

### M2 · 第二档：他会想起来

#### M2.1 · 浮现与遗忘（先做）

见 [design.md](design.md)「浮现怎么找」「遗忘」，接口见 [interfaces.md](interfaces.md) 的 llm、embedder、surfacing。

- [x] 迁移 003：`memories` 加 `embedding_model`、`recall_count`
- [x] 配置：`embedding.*`、`surfacing.*`、`decay.*`；依赖加 numpy
- [x] llm：OpenAI 兼容的 embedding 请求；没配时视为关闭
- [x] embedder：写记忆后算向量；启动时补算缺失的和换了模型的
- [x] surfacing：候选过滤、余弦相似度、字面匹配退路、遗忘现算、排序取舍、更新 `last_recalled_at` 和 `recall_count`（M2.1.1 改成用上了才更新）
- [x] context：浮现的记忆插在交接摘要和交接原话之间
- [x] Markdown 导出里的记忆显示被想起几次
- [x] README：embedding 二选一（在线平台 / 不配），本地 Ollama 只在进阶里提一句，不写步骤
- [x] 测试覆盖：用假的 embedding（例如按关键词生成固定向量）测相似排序；字面匹配退路；封存的和眼前的不浮现；遗忘让旧记忆排后但不消失；被想起后 recall_count 增加、下次排名上升；embedding 失败时退回字面匹配；换模型后补算

#### M2.1.1 · 用上了才算想起（接着做）

见 [design.md](design.md)「浮现怎么找」，接口见 [interfaces.md](interfaces.md) 的 surfacing 第 6 步和 inflow，prompt 见 [prompts/inflow.md](prompts/inflow.md)。

现在只要递进去就算想起一次，不相关的记忆会越递越牢。改成他真的用上了才算。

- [ ] chat：不再当场 `mark_recalled`；把这一轮最终递进上下文的记忆 id 写进回复的 `meta.surfaced_memory_ids`。发生交接的那一轮，只写交接后那一批
- [ ] inflow：这批轮次里递过记忆时，prompt 加「递给他的往事」一段，解析 `used_memory_ids`；没递过就不加
- [ ] inflow：只认这批里递过的 id；用上的 `recall_count + 1`，`last_recalled_at` 用递给它的那条回复的时间；同一批里同一条只加一次
- [ ] 降级：字段缺失、格式不对、整个解析失败、走原话降级路径，都当没用上
- [ ] `src/yuwen/prompts/inflow.md` 和文档里的 prompt 同步
- [ ] 旧数据：没有 `surfaced_memory_ids` 的回复照常流入，不报错
- [ ] 测试覆盖：递了没用不加次数；用了加次数且时间是那条回复的；交接那轮只记交接后那批；小模型返回没递过的 id 被忽略；`every_n_turns=3` 时三轮一起判断、同一条只加一次；解析失败不加次数；没递过记忆时 prompt 里没有那一段

#### M2.2 · 之后

- [ ] “这个忘掉吧”封存
- [ ] 日记工具 + 日历页面 + 日记回复
- [ ] 信物
- [ ] 感应事件
- [ ] 答应过的事会结束：memories 加 status 等字段；流入时识别新的承诺 / 待办；对话里或玩家一句话就能结案；浮现带上“这是哪天的状态”

### M3 · 第三档：他会想你

- [ ] 重铸：改写记忆，保留历史；守门人读重铸层
- [ ] 梦境：定时任务；写进按月的梦的合集，不单独开条目，不参与普通浮现，和上一个梦几乎一样的不记
- [ ] 唤醒：定时看一眼，自己决定发不发；发了就对外通知（webhook），给声音、形象、报备那个项目用
- [ ] 纪念日

## 还没想清楚的

- [ ] 交接带多少条原话：先默认 20，等实际用起来再调
- [ ] 情绪怎么标：参考实现先文字 + 数值都存，以后看哪个没用上再删
- [ ] 唤醒多久看一次：给别人的默认值，以及要不要让玩家自己调
- [ ] 本仓库自己用什么协议发布
- [ ] 放到服务器上给别人用之前，要先加登录：现在任何能打开网页的人都能看全部对话、导出记忆
