# MCP OAuth 接入说明

MCP 端点（`https://datak.kolya.icu/mcp`）是一个 **OAuth 2.1 Resource Server**：
agent 连接时必须用 **Cognito 登录**拿到 access token，服务端校验后放行，并把**登录者
本人的身份**（email）带进每次调用（日志 + `run_query` 返回的 `caller`）。

## 认证流程（自动发生）

```
agent 连 /mcp
  → 401 + WWW-Authenticate（指向 /.well-known/oauth-protected-resource/mcp）
  → agent 读元数据，得知授权服务器 = Cognito
  → agent 读 Cognito 的 openid-configuration，拿到 authorize/token 端点
  → 弹出 Cognito 登录（Authorization Code + PKCE）
  → 登录成功 → 拿 access token
  → agent 带 Bearer token 重连 /mcp → 通过，身份 = 登录的人
```

## 一个前提：Cognito 不支持 DCR

Cognito 没有动态客户端注册（RFC 7591），所以 agent **不能自动领 client_id**，必须用
我们**预建的公共 client_id**：

```
client_id: 5qqof1h3h2gnillpkkudqhtdqb   (public client, PKCE, 无 secret)
```

这个 client_id 不是密码，可公开、所有 agent 共用；真正的身份来自每个人各自的 Cognito
登录。（`/api/config` 里也会返回这个 client_id。）

## Amazon Q / Claude 接入

用 `mcp-remote` 作为桥接（它替 agent 完成 OAuth 握手，并支持手动指定 client_id 以
绕过 Cognito 无 DCR 的限制）。写入 `~/.aws/amazonq/mcp.json` 或客户端 MCP 配置：

```json
{
  "mcpServers": {
    "medical-data-service": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://datak.kolya.icu/mcp/", "3334",
        "--static-oauth-client-info",
        "{\"client_id\":\"5qqof1h3h2gnillpkkudqhtdqb\",\"token_endpoint_auth_method\":\"none\"}"
      ]
    }
  }
}
```

首次连接会弹出浏览器让你用 Cognito 登录；之后 `mcp-remote` 会缓存 token。

> 关键两点：
> 1. 用 `--static-oauth-client-info`（**不是** `--static-oauth-client-metadata`）：
>    前者直接用已有 client_id 并跳过 DCR；后者仍会尝试 DCR，Cognito 不支持会报
>    "does not support dynamic client registration"。
> 2. 固定回调端口 `3334`（URL 后的位置参数），否则 mcp-remote 随机端口会和
>    Cognito 白名单的 `http://localhost:3334/oauth/callback` 不匹配。

## 身份如何体现

- **access token 里没有 email**（Cognito 特性），服务端用 `admin-get-user` 按用户 id
  反查 email（带缓存）。
- 每次 `run_query` 的返回里带 `"caller": "<你的邮箱>"`，服务端日志也记录
  `run_query by=<邮箱> ...`。
- 注意：目前身份用于**审计/溯源**，不改变返回的数据（无按人分权/RLS）。

## 两种鉴权模式（可切换）

服务端用环境变量 `MCP_OAUTH_ENABLED` 控制：

| 模式 | MCP 鉴权 | 身份 | 适用 |
|------|---------|------|------|
| `MCP_OAUTH_ENABLED=true`（当前）| Cognito OAuth | 登录者本人（实时）| 需要真人身份 |
| `MCP_OAUTH_ENABLED=false` | API Key（`X-API-Key`）| 建 key 的人（创建时固定）| 简单机器接入 |

切换只需改任务定义的这个环境变量并重部署，无需改代码。

## 涉及的 AWS 资源

```
Cognito 用户池      us-east-1_13xlrjD4c (medical-dev-users)
门户 app client     2svmv1avufg5qvj5vrhc3c6b8u (管理页登录)
agent app client    5qqof1h3h2gnillpkkudqhtdqb (MCP OAuth，公共客户端)
任务角色权限        cognito-idp:AdminGetUser（反查 email）
```
