# Phase A 实施计划：退款/争议处理 Agent

> 基于 `WORKFLOW_AGENT_PLAN.md` 和对现有代码的阅读（`db.py` / `api.py` / `config.py` / `logger.py` / `setup_logging_db.py` / `migrate_to_postgres.py` / `tests/`）。
> 协作模式：**Ruirui 写代码，Claude 给骨架、接口签名、测试和 review**。每个里程碑独立可验证，一次只做一个。

---

## 0. 读代码后的关键发现（会影响设计）

| 发现 | 对设计的影响 |
|---|---|
| `db.py` 的 reader 连接是 **read-only session + SELECT-only 角色**；logger 角色是 INSERT-only | 沿用这个模式：新增第三个角色 `olist_workflow`，只能写 `workflow` schema。**读订单事实用 reader，写状态用 workflow 角色**，两者天然分离 |
| `setup_logging_db.py` 已有 "建 schema + 建角色 + GRANT，可重复执行不 drop" 的模板 | `setup_workflow_db.py` 直接照这个写法，不要用 `migrate_to_postgres` 那种 drop 重建 |
| `app_logs` 是独立 schema，reader 读不到 | 同理 `workflow` 是独立 schema，不会出现在 SQL agent 的 schema introspection 里（`get_live_schema` 只看 `public`），**不会污染 Analyst Agent** |
| Olist 数据是 **2016–2018** 的，且**没有退款表** | ① "当前时间"必须可注入（`as_of` 参数），否则所有订单都超出退款窗口；② 退款历史只能来自我们自己的 `workflow.disputes` 表 |
| 数据特点：`delivered` 96,478 / `canceled` 625 / `unavailable` 609 / `shipped` 1,107；`payment_value` 最大 13,664，均值 154 | 金额阈值要看分布来定（这是 Data Analyst 的强项，M0 让你自己做） |
| `payment_value` 在 `order_payments`，一个订单可有多条（`payment_sequential`） | 订单实付金额 = `SUM(payment_value)`，不是单行 |
| API 是 sync `def` + `X-API-Key` + pydantic，测试用 `TestClient` + monkeypatch，DB 测试"连不上就 skip" | workflow 的 API 和测试沿用同一风格 |
| 项目是**扁平模块**（`skillgen/` 是唯一的包） | 新建 `workflow/` 包是合理的（模块多、要和 Analyst 代码隔离） |

---

## 1. 架构

```
POST /disputes ─▶ service.submit_dispute()
                     │ 1. 读订单事实 (reader 角色, 只读)        ← facts.py
                     │ 2. 规则引擎 evaluate(facts, claim)        ← rules.py  (纯函数, 可解释)
                     │ 3. (可选) LLM 只做"分类 + 写说明 + 只能升级不能放行"  ← llm_assist.py
                     │ 4. transition(): 同一事务里  更新状态 + 写 audit  ← store.py / states.py
                     ▼
   auto_approved ──▶ execute_refund() ──▶ refund_executed   (mock payment, 幂等)
   pending_review ─▶ 持久化 approvals 行 ─▶ 人 POST /approvals/{id}/decision ─▶ approved/rejected
   rejected (终态)
```

**核心设计原则（面试要讲的）：**
1. **规则决定，LLM 不决定。** 批准/拒绝由确定性规则给出；LLM 只能①把自由文本理由归类 ②写人类可读说明 ③发现"矛盾/不确定"时**把决策往更严格的方向推**（auto_approve → pending_review），永远不能反向放行。这就是 "questioning AI output" 的落地。
2. **状态机在代码里显式约束**：一张 `ALLOWED_TRANSITIONS` 字典 + 一个唯一的 `transition()` 入口，任何地方不得直接 `UPDATE disputes SET status=...`。DB 层再加 `CHECK` 约束兜底。
3. **状态变更和审计记录在同一个事务里**：要么都成功要么都回滚，不会出现"状态变了但没记录"。
4. **审计表 append-only**：workflow 角色对 `audit_log` 只有 INSERT（没有 UPDATE/DELETE），和 `app_logs` 同一思路。
5. **审批持久化**：重启不丢；并发双击"批准"不会重复退款（`SELECT … FOR UPDATE` + 幂等键）。—— 这是对 Atlas 内存 `asyncio.Future` 审批的直接修复。

---

## 2. 状态机

```
submitted ──▶ auto_approved ──▶ refund_executed (终态)
    │              └──────────▶ refund_failed ──▶ refund_executed (重试成功)
    │                                        └──▶ pending_review (重试仍失败, 转人工)
    ├──▶ pending_review ──▶ approved ──▶ refund_executed / refund_failed
    │                  └──▶ rejected (终态)
    └──▶ rejected (终态)
```

- **`needs_human_review` 不单独做状态**，做成 `pending_review` 上的一个字段 `review_reason_type`：`policy`（规则要求人审，如大额）/ `agent_uncertain`（agent 自己判断不了，如规则矛盾）。这样状态图简单，同时留下 Michael 点名的"agent 说我判断不了"的痕迹，审计里也能区分。
- 终态：`rejected`、`refund_executed`。终态不可再迁移。

---

## 3. 数据模型（schema `workflow`）

```sql
disputes(
  id BIGINT IDENTITY PK, order_id TEXT, customer_id TEXT,
  reason_text TEXT, reason_category TEXT,         -- category 由规则/LLM 归类
  amount_requested NUMERIC(12,2),
  status TEXT CHECK (status IN (...7个状态...)),
  review_reason_type TEXT NULL,                    -- policy | agent_uncertain
  created_at, updated_at)

approvals(                                          -- 持久化审批
  id PK, dispute_id FK, status CHECK ('pending','approved','rejected'),
  requested_at, decided_at NULL, decided_by NULL, comment NULL,
  UNIQUE (dispute_id) WHERE status='pending')       -- 部分唯一索引: 一个争议同时只能有一个待审批

refund_attempts(
  id PK, dispute_id FK, idempotency_key TEXT UNIQUE, amount, result, response JSONB, attempted_at)

audit_log(                                          -- append-only
  id PK, ts, dispute_id, action, actor_type ('agent'|'human'|'system'), actor_id,
  from_status, to_status, rule_id NULL, reasoning TEXT, details JSONB)
```

角色 `olist_workflow`：`disputes`/`approvals`/`refund_attempts` 有 SELECT/INSERT/UPDATE，`audit_log` 只有 **INSERT + SELECT**（读审计要给 API 用；没有 UPDATE/DELETE）。

---

## 4. 规则引擎（草案，阈值在 M0 用数据确定）

输入 `OrderFacts`（订单状态、实付总额、购买/送达/预计送达时间、review 分数、该客户近 90 天已有退款数）+ `Claim`（reason、amount、as_of）。
输出 `Decision(outcome: auto_approve | needs_review | reject, rule_id, reasons: list[str], review_reason_type)`。**规则按优先级顺序执行，第一条命中即返回，`rule_id` 写进审计。**

| 优先级 | rule_id | 条件 | 结果 |
|---|---|---|---|
| 1 | R01_ORDER_NOT_FOUND | 订单不存在 | reject（或直接 400，不入库——你来定） |
| 2 | R02_DUPLICATE_OPEN | 该订单已有未终结的争议 | reject |
| 3 | R03_NOT_REFUNDABLE_STATUS | 订单状态为 canceled / unavailable（没有扣款可退） | reject |
| 3b | R03B_NOT_DELIVERED | 订单尚未送达（shipped/processing 等） | needs_review，`policy`（实现时新增） |
| 4 | R04_AMOUNT_NOT_COVERED | 无支付记录，或申请金额 > 实付总额 | reject（已定） |
| 5b | R05B_MISSING_DELIVERY_DATE | 已送达但没有送达日期，算不出窗口 | needs_review，`agent_uncertain`（实现时新增） |
| 5 | R05_OUTSIDE_WINDOW | 已送达且 `as_of` − 送达日 > 30 天 | reject |
| 6 | R06_CONTRADICTION | 理由与事实矛盾（如声称"没收到"但已送达且客户给了 5 星；声称"延迟"但实际提前送达） | needs_review，`agent_uncertain` |
| 7 | R07_HIGH_VALUE | 金额 ≥ 阈值（暂定 P95） | needs_review，`policy` |
| 8 | R08_REPEAT_REFUNDER | 近 90 天已退款 ≥ 2 次 | needs_review，`policy` |
| 9 | R09_LATE_DELIVERY_LOW_SCORE | 送达晚于预计 + review ≤ 2 + 金额 ≤ 小额阈值 | auto_approve |
| 10 | R10_SMALL_AMOUNT | 金额 ≤ 小额阈值且在窗口内 | auto_approve |
| 11 | R99_DEFAULT | 其他 | needs_review，`policy` |

> 默认倾向保守：**不确定就进人工，而不是自动批准**。

---

## 5. 里程碑（每个都小、可单独验证）

建议文件布局：

```
AI Agent/workflow/
  __init__.py
  states.py          # 状态枚举 + ALLOWED_TRANSITIONS + can_transition()
  models.py          # dataclass: OrderFacts, Claim, Decision
  facts.py           # 用 reader 查订单事实
  rules.py           # evaluate(facts, claim) -> Decision (纯函数)
  store.py           # DB 读写; transition() 原子入口
  payments_mock.py   # 假支付 API
  service.py         # 编排: submit / decide / execute_refund
  llm_assist.py      # M7: 分类 + 说明 + 只升级不放行
  api.py             # FastAPI APIRouter
AI Agent/setup_workflow_db.py     # 仿 setup_logging_db.py
AI Agent/tests/test_workflow_*.py
```

| # | 里程碑 | 你写什么 | 我做什么 | 验证 |
|---|---|---|---|---|
| **M0** | **数据勘探 + 政策定稿** | 用 SQL/pandas 看 `payment_value` 分布（P50/P90/P95/P99）、送达晚点比例、各状态数量，**自己定** 小额/大额阈值、窗口天数，写进 `docs/refund-policy.md`（1 页） | 给查询提示；帮你挑 8 个真实 `order_id` 作为测试夹具 | 政策文档 + 8 个 order_id 清单 |
| **M1** | **状态机（纯 Python，无 DB）** | `states.py`：`Status` 枚举、`ALLOWED_TRANSITIONS`、`can_transition()`、`InvalidTransition` 异常 | 写 `test_states.py` 的用例骨架（参数化：所有合法/非法迁移、终态不可出） | pytest 全绿，**还没碰数据库** |
| **M2** | **DB schema + 角色** | `setup_workflow_db.py`：4 张表、CHECK、部分唯一索引、`olist_workflow` 角色与 GRANT；`config.py` 加 `PG_WORKFLOW_*` | 对照 `setup_logging_db.py` review；提示 GRANT 细节 | 跑两次不报错；用 workflow 角色手动 `UPDATE audit_log` 应被拒 |
| **M3** | **原子 `transition()`** | `store.py`：开事务 → `SELECT … FOR UPDATE` → `can_transition` → UPDATE → INSERT audit → commit | 提供并发测试思路（两个线程同时批准同一个 approval） | 非法迁移回滚且无脏审计；并发测试只有一个成功 |
| **M4** | **规则引擎** | `models.py` + `facts.py` + `rules.py`，按第 4 节表格逐条实现；每条规则返回 `rule_id` + `reasons` | 写 `test_rules.py`：每条规则至少一个正例一个反例（用手工构造的 `OrderFacts`，不需要 DB） | 规则表驱动测试全绿 |
| **M5** | **服务编排 + mock 支付** | `payments_mock.py`（幂等键、可注入失败）、`service.py`：`submit_dispute` / `decide_approval` / `execute_refund` | 帮你想清楚 `refund_failed` 的重试路径 | 脚本跑通：自动批准→退款成功；失败→`refund_failed`→重试 |
| **M6** | **API 层** | `api.py` 的 router：`POST /disputes`、`GET /disputes/{id}`、`GET /disputes/{id}/audit`、`GET /approvals?status=pending`、`POST /approvals/{id}/decision`；挂到现有 `api.py`；审批人用 `X-Reviewer` 头标识 | 补 pydantic 校验和错误码约定（409 非法迁移等）；`TestClient` 测试 | OpenAPI 里能看到；API 测试全绿 |
| **M7** | **LLM 辅助（最后再加）** | `llm_assist.py`：① `reason_text → reason_category`（枚举）② 生成给审批人看的说明 ③ 矛盾检测，**只能把 auto_approve 升级为 needs_review** | 提供 prompt 模板和结构化输出 schema；写"LLM 试图放行也被拒绝"的测试 | LLM 失败/超时时系统仍按纯规则工作（降级） |
| **M8** | **端到端验证 + 审计回放** | 8 个 case 的 e2e 测试；写一个 `replay_audit(dispute_id)`，**只靠审计表**还原整个决策过程 | 帮你 review 审计字段是否足够 | 见第 6 节 |
| **M9** | **（可选）最简 UI + 部署** | Streamlit 一页：提交表单 + 待审批列表 + 审计时间线；更新 ECS 部署 | 参考现有 `app.py` / `api_client.py` | 本地能点完整流程；之后再做 ECS（部署前我会按 CLAUDE.md 先问你要的指导级别） |

> 建议节奏：M1 → M2 → M3 先把"骨架"做完（这三个最能体现工程能力），再做规则和 API。M0 可以和 M1 并行。

---

## 6. 测试用例（对应原计划的"5–8 个 case"）

| # | 场景 | 期望路径 | 重点断言 |
|---|---|---|---|
| T1 | 已送达、晚于预计、1 星、小额 | submitted → auto_approved → refund_executed | audit 里有 R09、有 refund_attempts |
| T2 | 已送达、金额 > 阈值 | submitted → pending_review(policy) | approvals 有一条 pending；**此时不应调用支付** |
| T3 | T2 之后人工批准 | pending_review → approved → refund_executed | audit 里 actor=human、`decided_by` 正确 |
| T4 | T2 之后人工拒绝 | pending_review → rejected | 终态；不可再迁移（再试应 409） |
| T5 | 订单已 canceled | submitted → rejected | rule_id=R03 |
| T6 | 超出 30 天窗口 | submitted → rejected | rule_id=R05（用 `as_of` 控制） |
| T7 | 理由"没收到"但已送达 + 5 星 | pending_review(agent_uncertain) | reasoning 明确写"我判断不了，原因…" |
| T8 | 异常输入：不存在的 order_id / 负金额 / 金额>实付 / 重复提交 | 400 或 reject | 不产生脏数据 |
| T9 | 支付 mock 失败 | auto_approved → refund_failed → 重试 → refund_executed | 幂等键保证不重复退款 |
| T10 | 并发：两个审批人同时批准 | 只有一个成功，另一个 409 | 只有一条 refund_attempt |

**完成标准**：对任意一个 dispute，只看 `audit_log` 就能回答 "谁、什么时候、基于哪条规则、为什么、状态从哪到哪"。

---

## 7. 范围边界（沿用原计划）

不接真实支付、不做多租户/多币种、UI 不追求好看、不引入多 agent 编排（Atlas 已证明过）、Phase B（KB 审核发布）等 Phase A 完成后再细化。

## 8. 面试叙事（做完之后能讲的）

- **Before/After**：只读分析 agent → 会执行动作、有审批闸门的系统，且**读/写权限在数据库层分离**。
- **对 Atlas 的修复**：内存审批 → 持久化审批 + 并发安全 + 幂等。
- **LLM 的边界**：规则决定，LLM 只能收紧不能放松；LLM 挂了系统仍可用。
- **可审计**：任何一笔退款都能从 append-only 审计表完整回放。

## 9. 需要你拍板的几个小决定（不阻塞 M0/M1）

1. R01（订单不存在）是直接 400，还是也入库并 reject 留痕？（我倾向 400，不污染表）
2. R04（申请金额 > 实付）是 reject 还是 needs_review？
3. 审批人身份先用 `X-Reviewer` 头（简单）还是做个 `reviewers` 表？（建议先用头）
4. M0 的阈值你想用 P90 还是 P95 作为"大额"线？
