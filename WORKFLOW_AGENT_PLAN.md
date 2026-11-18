# Workflow Agent Plan — Phase A + Phase B

> 这份文件是写给"未来在这个目录新开 session 的 Claude"看的交接文档，也是 Ruirui 自己的计划记录。新 session 打开时应该先读这份文件，再开始工作。

## 背景（为什么要做这个）

Ruirui 是 Data Analyst 转 MLE/AI Engineer，正在找工作。她现有的 portfolio：

- **Analyst AI Agent**（这个项目）：Text-to-SQL + 分析推理 agent，Postgres(Neon) + pgvector，已部署到 AWS ECS（`https://ol-0beb5a8ab00a4d08bc3788551ebb4dbf.ecs.us-east-2.on.aws/`），eval 从 27.8% → 70.6%。这个项目证明的是 **evaluation rigor**。
- **Atlas**（另一个 repo，bootcamp 作业）：多 agent 编排平台，orchestrator 协调多个 agent、人工审批（内存态，重启会丢）、风险打分。证明的是 **AI system design（多agent协调）**。
- **LLM Router Platform / LLM Infra Gateway**（另外两个 repo，bootcamp 作业）：模型路由、调度、成本权衡。证明的是 **model orchestration**。

一个认识她的人（前同事 Michael，正准备在他的 "Internal AI Ops" 团队招 Solution Engineer，团队做两件事：① agents for sales leads ② knowledge-base editor via vector DB）给了一条 portfolio 反馈，核心两点：

1. 现有三个项目（Analytics Agent / LLM Routing / 新项目）之间不该有太多重叠。缺的是：**真实业务流程里，agent 怎么安全地调用工具、写数据库、做审批、留审计记录** —— 不是"多 agent 互相协调"（Atlas 已经做了这个，不需要再加）。
2. 更深的一条：她应该把自我定位从"我 engineering background 够不够格"换成"我已经能构建 AI systems，接下来要证明自己能负责多大范围/复杂度的系统"。这个项目要证明的是 **business ownership**，不是"会不会写代码"。

Ruirui 决定做两个 workflow agent，复用这个目录现有的基础设施（Postgres、Streamlit/FastAPI 架构、AWS ECS 部署经验），**按顺序**做：

- **Phase A（先做，图快）**：退款/争议处理 Agent
- **Phase B（后做，补技术栈）**：知识库审核发布 Agent（引入 vector DB 的"写"侧，不是只读检索）

两个都要补的共同能力：**真实的审批闸门（approval gate）+ 状态机（state machine）+ 审计记录（audit trail）**，这是 Analyst AI Agent（纯只读查询）和 Atlas（agent 间协调但无持久化审批）都没有的。

---

## Phase A：退款/争议处理 Agent

### 场景
客户对订单提出退款/争议申请 → agent 查订单历史（**复用本项目现有的 Olist Postgres schema**：`orders`, `order_items`, `order_payments`, `order_reviews`）→ 按政策规则判断风险等级 → 低风险自动批准，高风险/异常走人工审批 → 执行"退款"（mock payment API，不接真实支付）→ 写审计记录。

### 为什么复用这个目录
- Postgres、schema、部署管线全部现成，不用重新搭基础设施。
- 叙事干净："我把一个只读分析 agent，升级成了一个会执行动作、带审批闸门的系统" —— 这个对比本身就是一个好的架构故事，面试时可以直接展示"before/after"。

### 核心组件（待细化，不是最终设计）
1. **Dispute 提交入口**：mock 一个"客户提交退款申请"的输入（order_id + 原因），不需要真实前端表单，命令行/简单API输入即可。
2. **规则引擎 / 风险判断**：读订单状态、金额、下单时间、历史退款次数等，判断"自动批准 / 需要人工审批 / 直接拒绝"三种结果。规则要写清楚、可解释（不是黑箱打分）。
3. **状态机**：明确定义 dispute 的生命周期状态（例如 `submitted` → `auto_approved` / `pending_review` → `approved` / `rejected` → `refund_executed`）。这是这个项目要证明的核心能力之一，状态转换要有代码层面的显式约束，不能用一堆 if-else 隐式实现。
4. **人工审批**：Atlas 里的审批是内存态 `asyncio.Future`，重启会丢 —— 这个项目要做成**持久化**的审批（写 Postgres 表），明确知道这是跟 Atlas 的差异点，也是一个面试时能讲"我知道之前那个设计的局限，这次怎么修"的故事。
5. **Mock 退款执行**：一个假的 payment API（返回固定成功/失败），不接真实支付网关。
6. **审计表**：每一次状态转换、每一次 agent 决策（包括为什么判定为高风险）都要写进一张 audit log 表，字段至少包含：时间、dispute_id、action、actor（agent/human）、reasoning、前后状态。
7. **"questioning AI output" 的具体体现**：对于金额异常大、或者规则判断矛盾的 case，agent 应该能明确说"这个我判断不了，需要人来看"，而不是硬给一个答案。这是 Michael 清单里明确点名的能力，应该在设计里留痕迹（比如一个专门的 `needs_human_review` 分支，带原因说明）。

### 明确排除在范围外（避免过度设计）
- 不接真实支付网关，不处理真实货币合规问题。
- 不做多租户/多币种。
- 不需要前端 UI 做得多漂亮，Streamlit 简单页面或命令行都可以，重点在后端状态机和审批逻辑。

### 验证方式（待细化）
- 至少设计 5-8 个测试 case，覆盖：自动批准、自动拒绝、需要人工审批、审批后执行、审批后拒绝、异常/边界输入。
- 跑一遍完整生命周期，确认审计表里每一步都有记录，且记录内容足够还原"agent 当时为什么这么判断"。

---

## Phase B：知识库审核发布 Agent（后做）

### 场景
有人提交一篇 KB 文章的修改/新增 → agent 用 vector DB 检索现有知识库内容，做"事实一致性/冲突检查"（新内容是否跟现有条目矛盾）→ 标出潜在冲突点 → 人工审批通过才真正"发布"（更新 vector DB 和一个 published 表）→ 写审计记录。

### 为什么做这个
- 直接对上 Michael 团队的第二块工作（knowledge-base editor via vector DB）。
- 补上 Ruirui 现有四个项目里一直没有的真实 gap：这个项目的 pgvector 目前只做"检索辅助 SQL 生成"（RAG for schema context），不是"vector DB 驱动的写入/审核流程"。这次要做的是后者。

### 核心组件（粗粒度，等 Phase A 做完再细化）
1. 复用本项目已有的 pgvector 基础设施，但这次 vector DB 的角色是"检索现有知识库做冲突检测"，不是"检索 schema"。
2. 审批闸门 + 状态机 + 审计记录 —— 复用 Phase A 做出来的设计模式（同一套状态机/审批/审计的"骨架"，换一个业务场景），这样两个项目之间的工程判断是一致的，而不是从零重新发明一套审批系统。
3. "冲突检测"本身可以先做得简单（比如 embedding 相似度 + LLM 判断是否矛盾），不需要复杂的事实核查系统。

### 当前状态
未开始设计，Phase A 完成后再回来细化这一部分。

---

## 协作方式（给接手的新 session 看）

Ruirui 是 Data Analyst 转 MLE/AI Engineer，没有软件工程背景。这个项目不是 bootcamp 作业（`AGENTS.md` 的"不代写"规则只明确适用于 Solely Bootcamp 目录），但她过去的偏好是：**默认教学模式，解释清楚、给骨架和局部提示，由她自己写，而不是直接整段代码**，根据她当时的精力/时间动态调整给多给少。

新 session 开始工作前，应该：
1. 先读这份文件和本项目现有代码（`AI Agent/` 下的 `agent.py`, `db.py`, `api.py` 等），搞清楚现有架构，不要臆测。
2. 跟 Ruirui 确认这次合作是不是还是"她自己写，你讲解引导"的模式，还是这次想要更快产出（她可能因为求职时间紧想要不同的节奏）——不要照搬 bootcamp 的默认设定，直接问她。
3. 一次只推进一个小目标，不要把 Phase A 的所有组件一次性甩给她。

## 当前状态

- Phase A：未开始，只有这份计划。下一步是跟 Ruirui 对齐第一个具体小目标（建议从"状态机设计"或"审计表 schema"开始，这两个是整个项目的骨架）。
- Phase B：未开始。
