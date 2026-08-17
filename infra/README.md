# AWS 部署（Terraform）

这套 Terraform 把本项目部署到 AWS：私有网络、容器化服务、托管前端、SSO 认证。

它部署的**就是这个 POC 本身**，只是跑在 AWS 上而不是本地。架构和真实生产一致，区别只在
资源规格——真实生产就是把 RPU、任务副本数、可用区数量放大。

> **当前状态**：`terraform validate` 通过，但**尚未执行过 `apply`**，也没有 state。
> 首次部署前请读 [部署前必须准备的东西](#部署前必须准备的东西)。

## 两种运行形态

```text
本地开发            uvicorn + Vite
AWS 部署（本目录）   ECS + ALB + Redshift 存储，单域名 HTTPS
```

业务逻辑（dbt 模型、语义层、MCP 服务、Vue 前端）两边完全一样。差别只是网络和
部署方式。本部署不带任何认证与权限控制。

## 它会创建什么

```text
VPC                    公私子网、NAT 网关、Internet Gateway
安全组                  ALB → 服务 → Redshift，最小权限链
Redshift Serverless    私有部署，publicly_accessible = false
Redshift 用量限制       按月，超限自动停用
ECS Fargate            集群 + 服务，一个镜像同时提供 Vue 前端、REST API 和 MCP
ALB                    HTTPS 入口（443），端口 80 只做 301 重定向
Route 53               app_domain 的 A / AAAA alias 记录
IAM 角色                任务角色 + 执行角色（Redshift Data API、读 Secret、写日志）
```

## 只有一个域名，只允许 HTTPS

Vue 前端、API 和 MCP 由同一个容器提供，共用一个主机名：

```text
https://app_domain/          Vue 前端门户
https://app_domain/api/...   REST API
https://app_domain/mcp       远程 MCP 端点
```

这样做的原因是浏览器的同源安全策略：如果前端是 HTTPS 而 API 是 HTTP，
`fetch` 会被当成 mixed content 直接拦截。共用一个 origin 同时也消掉了 CORS 配置。

因此三个变量是必填的，缺一个 `terraform plan` 就会失败：

```text
app_domain           UI 和 API 共用的主机名
route53_zone_name    该主机名所在的公有 hosted zone
acm_certificate_arn  覆盖该主机名的证书，必须和 ALB 同区域
```

端口 80 只返回 301，不提供任何内容。要彻底关掉它：

```hcl
enable_http_redirect = false
```

应用层也不接受明文：`REQUIRE_HTTPS=true` 时，服务读 `X-Forwarded-Proto`，
明文请求返回 308 重定向，并对 HTTPS 响应加 HSTS 头。健康检查路径 `/health` 例外，
因为 ALB 是直连容器用 HTTP 探活的。

## 网络设计

```text
Internet
    │
    │ https://app_domain
    ↓
  ALB (443, 公有子网；80 只做 301)
    │ 安全组：只允许 ALB
    ↓
  ECS Fargate (私有子网)
    │ 同一容器：Vue 前端静态文件 + FastAPI + MCP
    │ 安全组：只允许服务，端口 5439
    ↓
  Redshift Serverless (私有子网)
```

Redshift 不对外暴露。服务通过 NAT 网关访问 AWS API（Secrets Manager、Redshift Data
API），但入站只能来自 ALB。

## 目录结构

```text
infra/terraform/
├── versions.tf         provider 声明 + 远端 backend（默认注释掉）
├── variables.tf        输入变量
├── network.tf          VPC、子网、安全组
├── redshift.tf         私有 Redshift Serverless + 用量限制
├── iam.tf              ECS 任务角色和执行角色
├── ecs.tf              集群、任务定义、服务
├── alb.tf              公网 ALB + 目标组 + HTTPS 监听器
├── dns.tf              Route 53 A / AAAA alias 记录
├── dbt_runner.tf       一次性 dbt 构建任务（在 VPC 内跑 dbt）
├── bootstrap/          远端 state 的一次性初始化
└── environments/
    ├── dev.tfvars.example    dev 变量模板（脱敏，入库）
    ├── dev.tfvars            dev 的变量（含真实值，已被 .gitignore 忽略）
    ├── dev.s3.tfbackend      dev 的 state 位置
    ├── prod.tfvars           prod 的变量
    └── prod.s3.tfbackend     prod 的 state 位置
```

## 需要你填的值：什么时候改什么

`dev.tfvars` 含真实值、已被 `.gitignore` 忽略，仓库里不存在。首次使用先从模板复制：

```bash
cd infra/terraform/environments
cp dev.tfvars.example dev.tfvars   # 再填入你自己的真实值
```

代码里所有需要替换的地方都在下表。**其余文件不用改。**

| 文件 | 要填什么 | 什么时候填 |
|---|---|---|
| `environments/dev.s3.tfbackend`<br>`environments/prod.s3.tfbackend` | `bucket` | bootstrap 之后，首次 `init` 之前 |
| `environments/dev.tfvars`<br>`environments/prod.tfvars` | `app_domain`<br>`route53_zone_name`<br>`acm_certificate_arn`<br>`service_image`<br>`dbt_runner_image` | 首次 `plan` 之前 |

两组值的取得方式不同：

```text
backend 的 bucket    来自 bootstrap 的 terraform output
tfvars 的四个值       来自你已有的域名、证书和 ECR 镜像
```

`bucket` 里的 `CHANGEME` 和 tfvars 里的注释行是唯一两处占位符。填错的表现：

```text
bucket 没填     init 报 NoSuchBucket
tfvars 没填     plan 直接报错，因为四个变量都有 validation
```

后者是刻意设计的——HTTPS 单域名部署缺任何一个都跑不起来，宁可在 `plan` 阶段失败，
也不要部署出一个无法访问的环境。

### 为什么 backend 不写在代码里

`backend` 块**不支持变量插值**，所以 key 不能按环境动态生成。如果把 key 写死在
`versions.tf`，dev 和 prod 会共用一份 state：

```text
apply dev   → 写 medical/xxx/terraform.tfstate
apply prod  → 也写 medical/xxx/terraform.tfstate
              Terraform 认为 dev 的资源"需要改成 prod 的样子"
              → 把 dev 环境改掉或删掉
```

所以 `versions.tf` 里是空的 partial configuration，key 放在每个环境自己的
`.s3.tfbackend` 文件里：

```text
environments/dev.s3.tfbackend    key = "medical/dev/terraform.tfstate"
environments/prod.s3.tfbackend   key = "medical/prod/terraform.tfstate"
```

切环境必须显式重新 `init`，这个摩擦是有意留的。

## 远端 state 初始化

每个 AWS 账号执行一次：

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var 'state_bucket_name=medical-tfstate-<account-id>'
```

它会创建 S3 state 桶和 DynamoDB 锁表，并输出两个环境的 backend 配置：

```bash
terraform output -raw dev_backend_config  > ../environments/dev.s3.tfbackend
terraform output -raw prod_backend_config > ../environments/prod.s3.tfbackend
```

这一步会覆盖掉仓库里带 `CHANGEME` 的模板文件，也就填好了上表的第一项。

> **为什么要单独 bootstrap**：state 桶本身不能由使用它的那份 state 管理，否则删除时会
> 陷入循环依赖。所以它是独立的一小份配置。

## 部署

`init` 必须带上环境的 backend 配置，`plan` 和 `apply` 必须带上同一环境的 tfvars。
两者用的是不同的文件，**不要混**：

```bash
cd infra/terraform

# dev
terraform init  -backend-config=environments/dev.s3.tfbackend
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

切到 prod 需要重新 init，`-reconfigure` 让 Terraform 丢弃上一个环境的 backend 缓存：

```bash
terraform init -reconfigure -backend-config=environments/prod.s3.tfbackend
terraform plan  -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

> **不带 `-reconfigure` 会怎样**：Terraform 发现 backend 变了，会问你要不要把当前 state
> 迁移到新位置。回答 yes 就把 dev 的 state 复制成了 prod 的 state，两个环境从此指向同一
> 批资源。`-reconfigure` 直接跳过这个提问。

### 部署前必须准备的东西

`apply` 之前这几项必须落实，否则会失败或部署出不可用的环境：

```text
service_image        数据服务镜像，必须先构建并推 ECR
dbt_runner_image     dbt 构建镜像，必须先构建并推 ECR
app_domain           UI 和 API 共用的主机名
route53_zone_name    该主机名所在的 hosted zone
acm_certificate_arn  覆盖该主机名的证书，必须和 ALB 同区域
远端 backend         用 -backend-config 指向环境的 .s3.tfbackend
```

### 先构建两个镜像

这套部署需要两个镜像：数据服务（UI + API）和 dbt 构建器。都推到 ECR。

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

aws ecr create-repository --repository-name medical-data-service
aws ecr create-repository --repository-name medical-dbt-runner
aws ecr get-login-password | docker login --username AWS --password-stdin $REG

# 数据服务：上下文是 medical-data-service，-f backend/Dockerfile
cd medical-data-service
docker build --platform linux/amd64 -f backend/Dockerfile \
  -t $REG/medical-data-service:v1 .
docker push $REG/medical-data-service:v1

# dbt 构建器：上下文是 medical-olap-dbt
cd ../medical-olap-dbt
docker build --platform linux/amd64 -t $REG/medical-dbt-runner:v1 .
docker push $REG/medical-dbt-runner:v1
```

> **`--platform linux/amd64` 是必须的**。Fargate 跑 x86，在 Apple Silicon 上不加这个
> 参数会构出 arm64 镜像，任务启动时报 exec format error。

然后把两个镜像地址填进 tfvars：

```hcl
service_image    = "<account>.dkr.ecr.us-east-1.amazonaws.com/medical-data-service:v1"
dbt_runner_image = "<account>.dkr.ecr.us-east-1.amazonaws.com/medical-dbt-runner:v1"
```

### Redshift Serverless 要求 3 个可用区

`az_count` 必须 `>= 3`。Redshift Serverless 要求子网覆盖 3 个不同 AZ，否则 workgroup
创建失败：

```text
There aren't enough free IP addresses in subnets to allow this operation.
Make sure that there are at least 9 free IP addresses in 3 subnets.
```

`dev.tfvars` 和 `prod.tfvars` 都已设为 3。

### DNS 记录不能和已有记录冲突

`app_domain` 在 hosted zone 里必须没有同名记录。如果之前有别的服务用过这个名字（比如
一条指向旧 ELB 的 CNAME），`apply` 会报：

```text
InvalidChangeBatch: RRSet of type A ... conflicting RRSet of type CNAME
```

先确认那条记录确实废弃了，再删掉：

```bash
ZONE=$(aws route53 list-hosted-zones-by-name --dns-name <zone> \
  --query 'HostedZones[0].Id' --output text)
aws route53 list-resource-record-sets --hosted-zone-id $ZONE \
  --query "ResourceRecordSets[?starts_with(Name, '<app_domain>')]"
# 确认无用后用 change-resource-record-sets --change-batch 删除
```

## 灌数据：dbt runner

Redshift 是私有的（`publicly_accessible = false`），所以 dbt **不能从笔记本连**。
`dbt_runner.tf` 定义了一个一次性 Fargate 任务，在私有子网里跑
`dbt seed && dbt run && dbt test`，复用服务安全组（已被允许访问 Redshift 5439）。

`apply` 之后，在私有子网里跑这个任务：

```bash
WG=medical-dev-wg
SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=tag:Name,Values=medical-dev-private-*" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
SG=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=medical-dev-service-sg" \
  --query 'SecurityGroups[0].GroupId' --output text)

TASK=$(aws ecs run-task --cluster medical-dev-cluster \
  --task-definition medical-dev-dbt --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)

aws ecs wait tasks-stopped --cluster medical-dev-cluster --tasks "$TASK"
aws ecs describe-tasks --cluster medical-dev-cluster --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode' --output text   # 期望 0

aws logs tail /ecs/medical-dev-dbt --since 15m | tail -5     # 期望 PASS=NN
```

它是任务定义，不是服务：跑完即停。数据模型或 seed 变了就重跑一次。

> **dbt runner 镜像里保留 git**。git 用来安装 MetricFlow 的锁定 commit，而且 `dbt debug`
> 会检查 PATH 里有没有 git，没有就报错退出。数据服务镜像里 git 装完可以清掉，dbt 镜像不行。

## 资源规格

架构不变，靠变量调规格。当前 `dev.tfvars` 是**演示规格**：

| 变量 | 当前值 | 真实生产参考 | 影响 |
|---|---:|---:|---|
| `redshift_base_rpu` | 8 | 32–128 | 查询并发和延迟。8 是 Serverless 下限 |
| `redshift_max_rpu` | 16 | 256+ | 弹性上限 |
| `redshift_usage_limit_rpu_hours` | 300 | 按预算设 | 月度成本护栏，超限自动停用 |
| `service_desired_count` | 1 | 2+ | ECS 副本数。1 意味着部署期间有中断 |
| `az_count` | 3 | 3 | 可用区数量，同时决定 NAT 网关个数。Redshift Serverless 要求最少 3 |

演示规格和生产规格的差别只是数字。需要额外考虑的是：

```text
service_desired_count = 1   单副本，滚动更新期间会短暂不可用
无自动伸缩策略               生产应加 ECS Service Auto Scaling
无 Redshift 预留容量          长期稳定负载可以买预留降成本
```

## 环境变量：从本地切到 AWS

同一套代码，靠环境变量决定行为：

| 开关 | 本地开发 | AWS 部署 | 作用 |
|---|---|---|---|
| `QUERY_EXECUTION_ENABLED` | `true` | `true` | 是否真的执行查询 |
| `REDSHIFT_SECRET_ARN` 等 | 手动 export | Terraform 注入 | Redshift 连接信息 |

本部署不带认证与权限控制，MCP 端点对能访问域名的人开放。

## CI/CD

```text
.github/workflows/dbt.yml            dbt build + test 数据质量门禁
.github/workflows/data-service.yml   后端测试、前端构建、推 ECR、部署 ECS
.github/workflows/infra.yml          terraform fmt / validate / plan + 人工审批后 apply
```

`infra.yml` 刻意把 `plan` 和 `apply` 分开：`plan` 在 PR 上自动跑，`apply` 需要人工审批。
基础设施变更不应该被自动合并触发。

## 成本

主要成本来源，按量级排序：

```text
NAT 网关          按小时 + 流量计费，az_count=3 就是三个
Redshift          按 RPU-小时
ALB               按小时 + LCU
ECS Fargate       按 vCPU-小时和内存-小时（dbt runner 跑完即停，不持续计费）
Route 53          按托管区和查询计费，很低
```

不用的时候**销毁整套**比停单个组件更省，因为 NAT 网关和 ALB 是按小时计费的：

```bash
terraform destroy -var-file=environments/dev.tfvars
```

## 相关文档

| 想了解 | 看这里 |
|---|---|
| 第一次运行整个项目 | [GETTING-STARTED.md](../GETTING-STARTED.md) |
| MCP 数据服务架构 | [medical-data-service/README.md](../medical-data-service/README.md) |
| 数仓与语义层 | [medical-olap-dbt/README.md](../medical-olap-dbt/README.md) |
| 项目整体介绍 | [README.md](../README.md) |
