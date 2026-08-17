# medical-olap-dbt

POC dbt project for the medical sales warehouse.

## 文档

不熟悉业务背景，先看这份：

- [业务说明：这些表在讲一个什么故事](docs/business-model.md)

语义层配置、Entity、Dimension、指标类型、MetricFlow JOIN 逻辑、saved query 物化、
以及权限如何跟随语义层：

- [语义层入门：读懂 `models/dwd/schema.yml`](docs/semantic-layer.md)

## Setup

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp profiles.yml.example ~/.dbt/profiles.yml
```

Set database credentials through environment variables:

```bash
export REDSHIFT_USER=adminuser
export REDSHIFT_PASSWORD='...'
```

## Run

```bash
dbt debug
dbt seed
dbt run
dbt test
```

The target schemas are `sim_business`, `dwd`, `dws`, `ads` and `semantic`.

## 分层与语义层的边界

```text
sim_business   seed 原始数据
dwd            原子事实 + 一致性维度   ← 语义层建在这里
dws            派生缓存（只放可加字段，或由 saved query 物化）
ads            明细服务宽表（BI 下钻 + RLS/DDM 演示）
semantic       MetricFlow 时间骨架
```

指标**只在 DWD 定义一次**。在预汇总表上定义指标会丢失粒度，并让
`count_distinct` 和 `ratio` 无法还原。本项目实际踩过这个坑，重构前后的差异：

| 指标 | 建在 DWS 上 | 建在 DWD 上 |
|---|---:|---:|
| `order_count` | 6,029 | **3,032** |
| `payment_amount` | 587,541,651 | **252,213,635** |
| `campaign_cost` | 166,056,404 | **5,105,882** |
| `payment_rate` | 210.38% | **90.31%** |

原因和复现方式见 [语义层文档第 8 章](docs/semantic-layer.md#8-为什么语义层建在-dwd-而不是-dws)。

## MetricFlow 版本说明（重要）

本 POC 有意从 `main` 分支安装 MetricFlow。已验证并锁定的组合：

```text
metricflow:   main @ afab9635481dd3e32a9f1895f37ca83bde08a920 (0.213.0.dev0)
dbt-core:     1.12.2
dbt-redshift: 1.11.0
```

本项目使用 dbt 1.12+ 的 model-level Semantic Layer 配置：

- 语义配置嵌入对应 dbt model 的 `models[].semantic_model`；
- entities 和 dimensions 通过对应 model 的 `columns` 配置；
- simple metrics 直接写在该 model 的 `metrics` 下；
- 跨语义模型的 ratio / derived metrics 写在顶层 `metrics`；
- 不使用旧版顶层 `semantic_models`、`measures` 或 `type_params.measure`。

`dbt parse` 会把这些配置编译到 `target/semantic_manifest.json`，MetricFlow Python
Engine 再消费该 manifest 生成 Redshift SQL。

### 关于 saved query 的 export

`saved_queries` 和 `exports` 配置在 dbt-core 中都能解析，但**执行 export 物化是 dbt
平台的付费能力**（需要 job scheduler）。dbt-core 没有 `export` 或 `sl` 命令。这与数据库
无关，Redshift 本身是官方支持的平台。

本项目自己实现物化，因为 MetricFlow 原生支持按 saved query 名称编译：

```bash
python scripts/materialize_saved_queries.py --list
python scripts/materialize_saved_queries.py --dry-run
python scripts/materialize_saved_queries.py --execute
```

这样加速表和即席查询走同一套定义，口径不会漂。

生产化建议：锁定经过验证的 dbt/MetricFlow 版本组合，并在 CI 中加入 `dbt parse` 与
MetricFlow compile 冒烟测试。首次成功安装后请把 commit 记录到 `requirements.lock`。

## 脚本

```bash
python scripts/generate_seeds.py             # 生成三年测试数据
python scripts/dbt_parse.py                  # 绕开沙箱的 ProcessPoolExecutor 限制
python scripts/show_semantic_layer.py        # 列出语义模型与指标
python scripts/compile_metric_queries.py     # 编译 12 条业务查询
python scripts/verify_metric_values.py       # 新旧口径数字对比
python scripts/materialize_saved_queries.py  # 物化 saved query 到 dws
```

粒度问题的复现脚本：

```bash
python scripts/check_dws_grain_loss.py       # 预汇总导致订单数放大 1.99x
python scripts/check_payment_fanout.py       # 回款放大 2.33x，回款率 210%
python scripts/check_ads_fanout.py           # ADS 宽表金额放大 18.4x
```
