# 第一次运行这个项目

这份文档是操作手册。跟着做，从空环境到能查出指标、能让 AI agent 通过 MCP 取数。

两条路径，按你的目的选：

| 目的 | 需要 AWS | 大约耗时 | 跳到 |
|---|---|---|---|
| 只想看懂语义层和指标定义 | 不需要 | 15 分钟 | [路径 A](#路径-a本地验证不连数据库) |
| 想跑通完整数仓 + MCP 查询 | 需要 | 1 小时 | [路径 B](#路径-b完整环境含-redshift) |

**建议先做路径 A**。它不花钱、不需要 AWS，而且能验证 90% 的业务逻辑。

---

## 目录

- [0. 前置条件](#0-前置条件)
- [路径 A：本地验证（不连数据库）](#路径-a本地验证不连数据库)
- [路径 B：完整环境（含 Redshift）](#路径-b完整环境含-redshift)
- [虚拟环境怎么用](#虚拟环境怎么用)
- [环境变量清单](#环境变量清单)
- [常见问题](#常见问题)
- [用完记得清理](#用完记得清理)

---

## 0. 前置条件

### 必须

```bash
python3 --version    # 需要 3.13
uv --version         # 包管理器
```

没有 `uv` 的话：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 路径 B / C 额外需要

```bash
aws --version        # AWS CLI v2
node --version       # 需要 18+，只有前端要用
```

AWS 凭据：

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1

aws sts get-caller-identity     # 确认能连上
```

> **注意**：仓库里的文档和脚本曾经写死过旧 POC 的 Workgroup、Host、Secret ARN 和
> Security Group ID。那些资源**已经删除**。不要复用它们，否则会静默指向不存在的环境。
> 所有脚本现在都要求显式传入这些值。

---

## 路径 A：本地验证（不连数据库）

目标：看懂语义层怎么定义指标、怎么生成 SQL，并确认指标数字是对的。

### A1. 装 dbt 环境

```bash
cd medical-olap-dbt

uv venv --python 3.13 .venv
uv pip install -r requirements.txt
```

这个 venv 同时装了 dbt 和 MetricFlow，所以**它能跑本项目所有脚本**。

安装会拉 MetricFlow 的 `main` 分支，比较慢。已验证的版本组合记录在 `requirements.lock`。

### A2. 准备 profile

`dbt parse` 会读 profile，即使不真的连库：

```bash
cp profiles.yml.example ~/.dbt/profiles.yml
```

然后随便给两个占位凭据（parse 不会真连）：

```bash
export REDSHIFT_USER=placeholder
export REDSHIFT_PASSWORD=placeholder
```

### A3. 解析语义层

```bash
.venv/bin/python scripts/dbt_parse.py
```

> **为什么不用 `dbt parse`**：MetricFlow 的校验器会创建 `ProcessPoolExecutor`，在受限
> 环境下会抛 `PermissionError: [Errno 1] Operation not permitted`。这个包装脚本绕开该限制，
> 其他行为完全一致。你的环境如果不受限，直接 `.venv/bin/dbt parse --no-partial-parse` 也行。

成功后会生成 `target/semantic_manifest.json`，这是 MetricFlow 的唯一输入。

### A4. 看语义层里有什么

```bash
.venv/bin/python scripts/show_semantic_layer.py
```

预期 10 个语义模型、15 个指标：

```text
semantic models
  customers              primary=-                      "medical_dw"."dwd"."dim_customer"
  products               primary=-                      "medical_dw"."dwd"."dim_product"
  sales_reps             primary=-                      "medical_dw"."dwd"."dim_sales_rep"
  sales_regions          primary=-                      "medical_dw"."dwd"."dim_sales_region"
  channels               primary=-                      "medical_dw"."dwd"."dim_channel"
  sales_order_items      primary=order_item             "medical_dw"."dwd"."fct_sales_order_item"
  payments               primary=payment                "medical_dw"."dwd"."fct_payment"
  touchpoints            primary=touchpoint             "medical_dw"."dwd"."fct_campaign_touchpoint"
  prescriptions          primary=prescription_event     "medical_dw"."dwd"."fct_prescription"
  campaign_attribution   primary=campaign_attribution   "medical_dw"."dwd"."fct_campaign_attribution"
```

### A5. 编译业务查询

```bash
.venv/bin/python scripts/compile_metric_queries.py
```

12 条查询应全部成功。**这一步不连数据库**，只渲染 SQL。

留意其中两条：

```text
按大区名称看净销售额     → 自动 JOIN dim_sales_region
回款率（跨模型比率）     → 分子读 fct_payment，分母读 fct_sales_order_item
```

### A6. 核对指标数字

```bash
.venv/bin/python scripts/verify_metric_values.py
```

这一步最重要。它用 seeds 原始 CSV 重算每个指标，和重构前的口径对比：

```text
metric                     semantic layer            previous (DWS/ADS)
------------------------------------------------------------------------------
order_count                          3,032                     6,029  (1.99x)
payment_amount                 252,213,635               587,541,651  (2.33x)
campaign_cost                    5,105,882               166,056,404  (32.5x)
payment_rate                       90.31%                  210.38%
```

右边那列是**在预汇总表上定义指标的后果**。回款率 210% 显然不可能，这就是为什么语义层
必须建在原子事实层。原理见
[语义层文档第 8 章](medical-olap-dbt/docs/semantic-layer.md#8-为什么语义层建在-dwd-而不是-dws)。

### A7. 看归因窗口的取舍

```bash
.venv/bin/python scripts/check_attribution_window.py
```

```text
    window   matched items   bridge rows  avg tp/item       attributed   coverage
      none           5,910       103,611         17.5      275,116,066     98.5%
       90d           5,659        24,192          4.3      264,950,802     94.9%  <- configured
       30d           4,642        10,557          2.3      217,471,045     77.9%
```

窗口配在 `dbt_project.yml` 的 `attribution_lookback_days`。

### A8. 跑后端测试

```bash
cd ../medical-data-service/backend

uv venv --python 3.13 .venv
uv pip install -e .

.venv/bin/pytest -q
```

预期 `9 passed`。这些测试覆盖语义层查询、saved query 和 MCP 工具注册，**不需要数据库**。

### 路径 A 完成检查

```text
[ ] dbt parse 通过
[ ] 10 个语义模型、15 个指标
[ ] 12/12 查询编译成功
[ ] order_count = 3,032，payment_rate = 90.31%
[ ] 9 passed
```

到这里你已经验证了全部业务逻辑。接着往下才需要花钱。

---

## 路径 B：完整环境（含 Redshift）

目标：真的建表、真的查数。

### B1. 创建 Redshift Serverless

这里手工建一个**最小可用环境**，目的是快速跑通数仓和查询。

`infra/terraform/` 里有完整的 AWS 部署代码（私有子网 + ECS + ALB，单域名 HTTPS），但它需要先准备 ECR 镜像、ACM 证书和 Route 53 域名，而且**尚未验证过 `apply`**。
先用下面的命令跑通数据链路，再考虑完整部署：

```bash
export AWS_REGION=us-east-1
export NAMESPACE=medical-poc
export WORKGROUP=medical-poc-wg
export REDSHIFT_DATABASE=medical_dw

# 命名空间：托管管理员密码，避免明文
aws redshift-serverless create-namespace \
  --namespace-name "$NAMESPACE" \
  --db-name "$REDSHIFT_DATABASE" \
  --admin-username adminuser \
  --manage-admin-password

# 工作组：8 RPU 是最小值
aws redshift-serverless create-workgroup \
  --workgroup-name "$WORKGROUP" \
  --namespace-name "$NAMESPACE" \
  --base-capacity 8 \
  --publicly-accessible
```

> **`--publicly-accessible` 只适用于从本机直连调试**。`infra/terraform/redshift.tf` 里设的是
> `publicly_accessible = false`，由 ECS 安全组访问 5439。服务也跑在 AWS 上时，
> 不要开公网访问。

等它就绪（约 2-5 分钟）：

```bash
aws redshift-serverless get-workgroup --workgroup-name "$WORKGROUP" \
  --query 'workgroup.status' --output text
```

拿到连接信息：

```bash
export REDSHIFT_WORKGROUP="$WORKGROUP"

export REDSHIFT_HOST=$(aws redshift-serverless get-workgroup \
  --workgroup-name "$WORKGROUP" \
  --query 'workgroup.endpoint.address' --output text)

export REDSHIFT_SECRET_ARN=$(aws redshift-serverless get-namespace \
  --namespace-name "$NAMESPACE" \
  --query 'namespace.adminPasswordSecretArn' --output text)

echo "host   = $REDSHIFT_HOST"
echo "secret = $REDSHIFT_SECRET_ARN"
```

### B2. 加一条成本护栏

忘关会一直计费，先设上限：

```bash
aws redshift-serverless create-usage-limit \
  --resource-arn $(aws redshift-serverless get-workgroup \
      --workgroup-name "$WORKGROUP" --query 'workgroup.workgroupArn' --output text) \
  --usage-type serverless-compute \
  --amount 50 \
  --period monthly \
  --breach-action deactivate
```

50 RPU-小时后自动停用。跑完本文档全部流程大约用 2-3 RPU-小时。

### B3. 开放你的 IP

公网访问需要放行当前 IP：

```bash
bash scripts/update-redshift-ip.sh
```

> 这个脚本只在 Redshift 开了公网访问时需要。它读你的出口 IP 并更新安全组，换网络
> （比如切 WiFi）后要重新跑。服务跑在 ECS 里时不需要它。

### B4. 取出数据库凭据

```bash
export REDSHIFT_USER=$(aws secretsmanager get-secret-value \
  --secret-id "$REDSHIFT_SECRET_ARN" \
  --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')

export REDSHIFT_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id "$REDSHIFT_SECRET_ARN" \
  --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
```

### B5. 更新 dbt profile

`profiles.yml.example` 里的 host 是已删除的旧环境，必须改：

```bash
cd medical-olap-dbt
sed "s|host: .*|host: $REDSHIFT_HOST|" profiles.yml.example > ~/.dbt/profiles.yml

.venv/bin/dbt debug        # 应该看到 All checks passed!
```

### B6. 生成测试数据（可选）

`seeds/` 里已经有三年数据，直接用就行。想重新生成：

```bash
.venv/bin/python scripts/generate_seeds.py
```

数据规模和预埋的业务故事线见
[业务说明文档](medical-olap-dbt/docs/business-model.md#9-数据里预埋的业务故事)。

### B7. 构建数仓

```bash
.venv/bin/dbt seed --full-refresh     # 9 张 seed 表，约 19,000 行
.venv/bin/dbt run                     # 建 dwd / dws / ads / semantic
.venv/bin/dbt test                    # 数据质量测试
```

`dbt run` 之后 schema 应该是：

```text
sim_business   seed 原始数据
dwd            原子事实 + 一致性维度   ← 语义层建在这里
dws            派生缓存
ads            明细服务宽表
semantic       MetricFlow 时间骨架
```

重点检查这条测试：

```bash
.venv/bin/dbt test --select assert_attribution_weight
```

它验证每条订单明细的归因权重加起来等于 1。通过说明收入分摊没有重复计算。

### B9. 物化加速表（可选）

```bash
cd medical-olap-dbt

.venv/bin/python scripts/materialize_saved_queries.py --list
.venv/bin/python scripts/materialize_saved_queries.py --dry-run
.venv/bin/python scripts/materialize_saved_queries.py --execute
```

> **为什么不用 `dbt build`**：`saved_queries` 和 `exports` 在 dbt-core 里能解析，但
> **执行 export 需要 dbt 平台付费方案 + job scheduler**。dbt-core 没有 `export` 或
> `sl` 命令。这与数据库无关，Redshift 本身是官方支持的平台。所以这里自己实现物化：
> 用 MetricFlow 编译 saved query，再包成 `CREATE TABLE AS`。加速表和即席查询走同一套
> 定义，口径不会漂。

### B10. 启动后端

```bash
cd ../medical-data-service/backend

export REDSHIFT_WORKGROUP REDSHIFT_DATABASE REDSHIFT_SECRET_ARN REDSHIFT_HOST
export METRICFLOW_MANIFEST_PATH=../../medical-olap-dbt/target/semantic_manifest.json

# 先只读模式，确认服务能起来（不执行真实查询）
export QUERY_EXECUTION_ENABLED=false

.venv/bin/uvicorn app.main:app --reload --port 8000
```

另开一个终端验证：

```bash
# 可用指标和维度
curl -s localhost:8000/api/metrics | python3 -m json.tool | head -20

# 生成 SQL（不执行）
curl -s -X POST localhost:8000/api/query/preview \
  -H 'Content-Type: application/json' \
  -d '{"metrics":["net_sales_amount"],"group_by":["sales_region__sales_region_name"]}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["sql"])'
```

确认返回的 SQL 里有 `dwd.fct_sales_order_item` 和自动 JOIN 的 `dim_sales_region`。

### B11. 打开取数开关

SQL 生成对了，再允许真的执行（无认证、无权限控制）：

```bash
export QUERY_EXECUTION_ENABLED=true
# 重启服务

curl -s -X POST localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"metrics":["net_sales_amount","order_count"],"group_by":["sales_region__sales_region_name"]}' \
  | python3 -m json.tool
```

**关键校验**：`order_count` 加起来应该接近 3,032。如果明显偏大，说明语义层没生效。

### B12. 连接 MCP 和启动前端

MCP 端点在 `http://localhost:8000/mcp`，可直接把本地 agent（Amazon Q / Claude）
连上去（配置见 [medical-data-service/README.md](medical-data-service/README.md)）。

启动 Vue 前端门户（可选，无需登录）：

```bash
cd ../frontend
npm install
npm run dev
```

访问 http://localhost:5173，里面有连接指引、指标目录和查询实验台。

### 路径 B 完成检查

```text
[ ] dbt debug 全绿
[ ] dbt seed / run / test 通过
[ ] assert_attribution_weight 通过
[ ] /api/query/preview 返回 dwd.fct_sales_order_item
[ ] /api/query 的 order_count 约 3,032
[ ] /mcp 能被 agent 连接，工具可调用
```

---

## 虚拟环境怎么用

两个独立 venv：

```text
medical-olap-dbt/.venv                  dbt + MetricFlow
medical-data-service/backend/.venv      FastAPI + MetricFlow（没有 dbt）
```

`medical-olap-dbt/.venv` 装了两者，所以**它能跑本项目全部脚本**。反过来不行：

```bash
medical-data-service/backend/.venv/bin/python -c "import dbt"
# ModuleNotFoundError: No module named 'dbt'
```

### 推荐做法：直接调解释器

```bash
cd medical-olap-dbt
.venv/bin/python scripts/verify_metric_values.py
.venv/bin/dbt run
```

不用记当前激活了哪个环境，在脚本和 CI 里也更可靠。

### 或者激活

```bash
cd medical-olap-dbt
source .venv/bin/activate
dbt run
deactivate
```

两个环境不要混用。来回切之前先 `deactivate`。

### 哪些脚本需要数据库

```bash
# 不连库，随时可跑
scripts/verify_metric_values.py
scripts/check_attribution_window.py
scripts/check_dws_grain_loss.py
scripts/check_payment_fanout.py
scripts/check_ads_fanout.py
scripts/render_attribution_sql.py
scripts/show_semantic_layer.py
scripts/compile_metric_queries.py

# 要读 profiles.yml（不真连）
scripts/dbt_parse.py

# 要 AWS 凭据
scripts/materialize_saved_queries.py --execute
```

---

## 环境变量清单

### dbt

```bash
REDSHIFT_USER          数据库用户
REDSHIFT_PASSWORD      数据库密码
```

### 数据服务

```bash
REDSHIFT_WORKGROUP           Serverless 工作组名
REDSHIFT_DATABASE            默认 medical_dw
REDSHIFT_SECRET_ARN          管理员凭据 Secret
REDSHIFT_HOST                Redshift 端点地址
METRICFLOW_MANIFEST_PATH     语义清单路径
AWS_REGION                   默认 us-east-1
QUERY_EXECUTION_ENABLED      是否允许真的执行查询（默认 true）
```

---

## 常见问题

### `Env var required but not provided: 'REDSHIFT_USER'`

`dbt parse` 也要读 profile。占位值就行：

```bash
export REDSHIFT_USER=placeholder REDSHIFT_PASSWORD=placeholder
```

### `PermissionError: [Errno 1] Operation not permitted`

MetricFlow 校验器创建 `ProcessPoolExecutor` 被环境限制。用包装脚本：

```bash
.venv/bin/python scripts/dbt_parse.py
```

### `Server refuses SSL` / 连不上

按顺序查：

```bash
# 1. 工作组在跑吗
aws redshift-serverless get-workgroup --workgroup-name "$REDSHIFT_WORKGROUP" \
  --query 'workgroup.status' --output text

# 2. 你的 IP 放行了吗（换过网络就要重跑）
bash scripts/update-redshift-ip.sh

# 3. profile 里的 host 对吗
grep host ~/.dbt/profiles.yml
```

### `error: set REDSHIFT_WORKGROUP REDSHIFT_SECRET_ARN REDSHIFT_HOST`

E2E 脚本刻意不给默认值。旧 POC 的资源已删除，用默认值会静默指向不存在的环境。

### `MetricFlow semantic manifest not found`

先跑 parse，再指对路径：

```bash
cd medical-olap-dbt && .venv/bin/python scripts/dbt_parse.py
export METRICFLOW_MANIFEST_PATH=$PWD/target/semantic_manifest.json
```

### `query execution is disabled`

服务没开执行开关。确认 SQL 正确后打开：

```bash
export QUERY_EXECUTION_ENABLED=true
```

### 指标数字明显偏大

大概率是绕过了语义层直接查了 DWS 或 ADS。用这三个脚本确认放大倍数：

```bash
.venv/bin/python scripts/check_dws_grain_loss.py     # order_count 1.99x
.venv/bin/python scripts/check_payment_fanout.py     # payment 2.33x
.venv/bin/python scripts/check_ads_fanout.py         # net_sales 18.4x
```

---

## 用完记得清理

Redshift Serverless 按 RPU-小时计费，**忘关会一直花钱**。

```bash
aws redshift-serverless delete-workgroup --workgroup-name "$REDSHIFT_WORKGROUP"

# 等工作组删完再删命名空间
aws redshift-serverless delete-namespace \
  --namespace-name "$NAMESPACE" --no-final-snapshot
```

还要检查这些残留：

```bash
# 用量限制
aws redshift-serverless list-usage-limits --resource-arn <workgroup-arn>

# 托管 Secret
aws secretsmanager list-secrets \
  --filters Key=name,Values=redshift --query 'SecretList[].Name'

# 安全组和 ENI（删工作组后可能残留）
aws ec2 describe-security-groups --filters Name=group-name,Values='*medical*' \
  --query 'SecurityGroups[].GroupId'
aws ec2 describe-network-interfaces \
  --filters Name=description,Values='*redshift*' \
  --query 'NetworkInterfaces[].NetworkInterfaceId'
```

确认清空：

```bash
aws redshift-serverless list-workgroups --query 'workgroups[].workgroupName'
aws redshift-serverless list-namespaces --query 'namespaces[].namespaceName'
```

---

## 接下来读什么

| 想了解 | 看这里 |
|---|---|
| 这些表在讲什么业务故事 | [业务说明](medical-olap-dbt/docs/business-model.md) |
| 语义层怎么定义指标 | [语义层入门](medical-olap-dbt/docs/semantic-layer.md) |
| dbt 项目怎么用 | [medical-olap-dbt/README.md](medical-olap-dbt/README.md) |
| MCP 数据服务架构 | [medical-data-service/README.md](medical-data-service/README.md) |
| AWS 部署 | [infra/README.md](infra/README.md) |
| 项目整体介绍 | [README.md](README.md) |
