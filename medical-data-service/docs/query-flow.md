# 一个问题是怎么变成数据的：自然语言 → MetricFlow 查询 → Redshift

本文解释：当人类用自然语言问一个业务问题时，它是**怎么一步步被拆成 MetricFlow 查询、
生成 SQL、在 Redshift 执行并返回**的，以及整条调用链上每一环各自负责什么。

## 一句话先说清楚

> **「自然语言 → MetricFlow 查询参数」这一步不是本服务的代码做的，是 agent 里的 LLM 做的。**

MCP 服务只接收**已经结构化好的参数**（`metrics` + `group_by` 列表），
再交给 MetricFlow 编译成 SQL、在 Redshift 执行。语义层保证口径统一，LLM 只能引用
合法的指标/维度名字，改不了口径。

## 例子

人类在 Amazon Q / Claude 里问：

> **「按大区看今年各区域的净销售额和订单数，哪个区最高？」**

## 整条调用链

```
┌─ 1. 人类自然语言提问
│      "按大区看净销售额和订单数"
│
├─ 2. Agent(LLM) 先探目录（可选但常见）
│      MCP 调用 list_metrics() / list_dimensions()
│      ← 服务返回 15 个指标、35 个维度 + 中文描述
│      LLM 据此把 "净销售额" → net_sales_amount
│                 "订单数"   → order_count
│                 "大区"     → sales_region__sales_region_name
│
├─ 3. Agent(LLM) 把自然语言翻译成结构化 mf 参数   ★ 拆解发生在这里
│      run_query(
│        metrics=["net_sales_amount", "order_count"],
│        group_by=["sales_region__sales_region_name"],
│        limit=1000)
│      经 MCP over HTTP → https://<域名>/mcp
│
├─ 4. MCP 服务端接到工具调用
│      app/mcp/server.py :: run_query()
│           → app/service.py :: DataService.run_query()
│
├─ 5. 生成 SQL（MetricFlow 编译）
│      DataService.generate_sql()
│           → MetricFlowClient.explain()            (app/metricflow/client.py)
│               MetricFlowQueryRequest.create(
│                   metric_names=[...], group_by_names=[...], limit=...)
│               engine.explain(request)             ← MetricFlow 引擎
│               RedshiftSqlPlanRenderer 渲染成 Redshift 方言 SQL
│
├─ 6. 执行 SQL
│      RedshiftDataApi.query_records(sql)           (app/redshift/client.py)
│           走 Redshift Data API，返回 {columns, rows, row_count}
│
├─ 7. 结果原路返回给 agent
│      {metrics, group_by, sql, columns, rows, row_count}
│
└─ 8. Agent(LLM) 读懂行数据，用自然语言回答
       "华东最高，净销售额 X，其次华南…"
```

## 第 3 步「拆解」的本质

LLM 做的是一个**受约束的翻译**——把口语映射到语义层里**合法的名字**：

| 人类说的 | LLM 映射到（来自 list_metrics / list_dimensions） |
|---------|--------------------------------------------------|
| 净销售额 | `net_sales_amount`（指标） |
| 订单数 | `order_count`（指标） |
| 按大区 / 各区域 | `sales_region__sales_region_name`（维度） |
| 今年 | 时间维度过滤（当前 `run_query` 未开放 `where` 参数，多落在时间维度分组或全量） |

一个 MetricFlow 查询本质就是这几个字段：`metrics`、`group_by`、`limit`
（还可扩展 `where`、`order_by`）。所以「拆解」= LLM 填这几个槽位。

## 第 5 步 MetricFlow 生成的 SQL（示意）

```sql
SELECT
  sales_region_name        AS sales_region__sales_region_name,
  SUM(net_amount)          AS net_sales_amount,
  COUNT(DISTINCT order_id) AS order_count
FROM "dwd"."fct_sales_order_item" fct
LEFT JOIN "dwd"."dim_sales_region" reg
  ON fct.sales_region_id = reg.sales_region_id
WHERE order_status = 'COMPLETED'
GROUP BY sales_region_name
LIMIT 1000
```

MetricFlow 自动做了三件人不用管的事：

1. 从**原子事实表** `dwd.fct_sales_order_item` 取数（不是 ADS 宽表，避免重复计数）；
2. 自动 JOIN `dim_sales_region`，把 region_id 换成可读的大区名；
3. 带上指标定义里写死的过滤条件 `order_status = 'COMPLETED'`。

## 各环节职责

| 环节 | 组件 | 负责什么 |
|------|------|---------|
| 语义理解、填槽 | Agent 的 LLM | 把自然语言映射成合法的 metrics/group_by |
| 名字校验、目录 | `list_metrics` / `list_dimensions` | 告诉 LLM 有哪些合法名字及含义 |
| 口径与 SQL | MetricFlow + 语义层(dbt) | 决定聚合方式、过滤、查哪张表、自动 JOIN |
| 执行取数 | Redshift Data API | 在 Redshift 上跑 SQL，返回列 + 行 |
| 结果解读 | Agent 的 LLM | 把行数据变成自然语言回答 |

## 为什么这样设计

- **口径统一**：指标的聚合方式、过滤条件、查哪张表都在语义层（dbt YAML）定义好，
  LLM 只能引用名字、改不了口径，所以「销售额」永远算得一样。
- **LLM 不写 SQL**：它只填 `metrics` / `group_by` 槽位，避免幻觉出错误 SQL 或查错表
  导致数字翻倍。
- **加指标零改代码**：新指标在 dbt YAML 定义 + `dbt parse`，manifest 更新后
  `list_metrics` 自动带出，LLM 立刻能用（详见 [../README.md](../README.md)）。

## 相关代码

```text
app/mcp/server.py        MCP 工具入口：list_metrics / list_dimensions / preview_sql / run_query
app/service.py           DataService：目录 + generate_sql + run_query
app/metricflow/client.py MetricFlowClient：读 manifest、构造查询请求、渲染 SQL
app/redshift/client.py   RedshiftDataApi：执行 SQL、返回列+行
```
