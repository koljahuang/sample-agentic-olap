# Medical Data Service — 远程 MCP 数据服务

把医药销售数仓的 MetricFlow 语义层暴露成一个**远程 HTTP MCP server**。本地 agent
（Amazon Q / Claude Desktop 等）连上后，人类用自然语言问数和分析。

```text
本地 agent ──(MCP + API Key)──▶ /mcp ──▶ MetricFlow 语义层 ──▶ Redshift
人（浏览器）─(Cognito 登录)──▶ Vue 前端 + /api/* ──▶ 同一域名
```

## 访问控制（两道门，无数据行级权限）

没有 RLS/DDM——过了门之后所有指标都可见。两道门只区分「人」和「机器」：

| 路径 | 谁用 | 鉴权 |
|------|------|------|
| `/` `/api/config` `/health` | 公开 | 无 |
| `/api/info` `/api/metrics` `/api/query*` | 人（浏览器） | Cognito 登录 (JWT) |
| `/api/admin/keys` | 管理员 | Cognito + 邮箱 == `ADMIN_EMAIL` |
| `/mcp` | agent（机器） | **Cognito OAuth**（Bearer access token）|

- **管理页面走 Cognito**：人打开前端要用 Cognito Hosted UI 登录。
- **MCP 走 Cognito OAuth**：agent 连接时弹 Cognito 登录（Authorization Code + PKCE），
  每次调用带上**登录者本人身份**（email）——详见 [docs/mcp-oauth.md](docs/mcp-oauth.md)。
- 也可用环境变量 `MCP_OAUTH_ENABLED=false` 切回 **API Key**模式（`X-API-Key`，
  存于 Secrets Manager，管理员在前端「API 密钥」页管理）。

## 组成

```text
backend/app/
  main.py            FastAPI：挂载 MCP(/mcp) + REST(/api/*) + 前端静态
  mcp/server.py      FastMCP：list_metrics / list_dimensions / preview_sql / run_query
  service.py         核心：语义层生成 SQL + Redshift 执行
  metricflow/        MetricFlow 客户端（读 semantic_manifest.json 生成 SQL）
  redshift/client.py Redshift Data API（执行 SQL、返回列+行）
frontend/            Vue 3 + shadcn-vue 门户（连接指引 / 指标目录 / 查询实验台）
```

## MCP 工具

| 工具 | 作用 |
|------|------|
| `list_metrics` | 列出所有指标及说明 |
| `list_dimensions` | 列出可分组维度及说明（`entity__dimension` 形式） |
| `preview_sql` | 只生成 SQL，不执行 |
| `run_query` | 在 Redshift 上执行并返回 `{columns, rows, row_count}` |

## 环境变量

```text
COGNITO_ISSUER         https://cognito-idp.<region>.amazonaws.com/<UserPoolId>
COGNITO_CLIENT_ID      Cognito app client id
COGNITO_DOMAIN         <prefix>.auth.<region>.amazoncognito.com
ADMIN_EMAIL            只有这个邮箱能创建/吊销 API key
MCP_API_KEYS_SECRET    Secrets Manager 里存 key 的 secret 名（默认 medical/dev/mcp-api-keys）
REDSHIFT_WORKGROUP / REDSHIFT_DATABASE / REDSHIFT_SECRET_ARN / AWS_REGION
QUERY_EXECUTION_ENABLED  是否真的执行查询（默认 true）
METRICFLOW_MANIFEST_PATH 语义清单路径（镜像内默认 /app/semantic_manifest.json）
```

## 本地运行

后端（需要 Redshift 环境变量才能真正执行查询）：

```bash
cd backend
uv sync
export METRICFLOW_MANIFEST_PATH=../../medical-olap-dbt/target/semantic_manifest.json
export AWS_REGION=us-east-1
export REDSHIFT_WORKGROUP=medical-poc-wg REDSHIFT_DATABASE=medical_dw
export REDSHIFT_SECRET_ARN=<namespace admin secret arn>
export QUERY_EXECUTION_ENABLED=true
export COGNITO_ISSUER=... COGNITO_CLIENT_ID=... COGNITO_DOMAIN=... ADMIN_EMAIL=you@example.com

uv run uvicorn app.main:app --reload --port 8000
```

- MCP 端点：`http://localhost:8000/mcp`
- REST：`GET /api/info`、`GET /api/metrics`、`POST /api/query/preview`、`POST /api/query`

也可脱离 FastAPI 单独跑 MCP：`uv run python -m app.mcp.server`（Streamable HTTP）。

前端：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 代理到 8000
```

## 把 MCP 接到本地 agent

远程 MCP 地址：`https://<你的域名>/mcp`（本地为 `http://localhost:8000/mcp`）。

Amazon Q / Claude 的 MCP 配置（写入 `~/.aws/amazonq/mcp.json` 或客户端配置）。
`X-API-Key` 的值在前端「API 密钥」页创建：

```json
{
  "mcpServers": {
    "medical-data-service": {
      "url": "https://<你的域名>/mcp",
      "transport": "http",
      "headers": { "X-API-Key": "mk_live_..." }
    }
  }
}
```

连上后在对话里直接问，例如：

> 按大区看今年的净销售额和回款率，并分析哪个大区 ROI 最高

agent 会调用 `list_metrics` / `run_query` 等工具，生成 SQL、取数并分析。

一个自然语言问题**怎么一步步被拆成 MetricFlow 查询、生成 SQL、执行返回**，
完整调用链和例子见 [docs/query-flow.md](docs/query-flow.md)。

## 部署

`backend/Dockerfile` 一体化打包前端 + 后端 + 语义层 manifest。修改语义层后，
先 `dbt parse`，再跑 `backend/refresh-manifest.sh` 刷新打包的 manifest，然后重建镜像。

ECS 任务环境变量含 Cognito、`ADMIN_EMAIL`、`MCP_API_KEYS_SECRET` 等；任务角色
需对那个 Secrets Manager secret 有 Get/Put/Describe 权限。
