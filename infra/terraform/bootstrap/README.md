# Terraform 远端 state 初始化

创建主配置所用的远端 backend：S3 state 桶 + DynamoDB 锁表。**每个 AWS 账号跑一次。**

这份配置刻意使用**本地 state**——它创建的正是远端 state 的存放位置，不能由自己管理，
否则销毁时会陷入循环依赖。

## 创建

```bash
cd infra/terraform/bootstrap

terraform init
terraform apply -var 'state_bucket_name=medical-tfstate-<account-id>'
```

`state_bucket_name` **没有默认值**，因为 S3 桶名全球唯一，给默认值会让人在另一个账号里
撞名。其余变量都有默认值：

```text
region            us-east-1
lock_table_name   medical-tf-lock
```

## 生成两个环境的 backend 配置

apply 之后把输出写进文件，主配置 `init` 时会用到：

```bash
terraform output -raw dev_backend_config  > ../environments/dev.s3.tfbackend
terraform output -raw prod_backend_config > ../environments/prod.s3.tfbackend
```

这会覆盖掉仓库里带 `CHANGEME` 的模板。

两个环境的 `key` 不同是关键：

```text
medical/dev/terraform.tfstate
medical/prod/terraform.tfstate
```

共用一个 key 会让 `apply prod` 把 dev 的资源改掉或删掉。`versions.tf` 里的 backend 是空的
partial configuration，正是为了强制按环境分开——backend 块不支持变量插值，key 无法动态生成。

然后回到主配置：

```bash
cd ..
terraform init -backend-config=environments/dev.s3.tfbackend
```

## 销毁

变量值必须给，即使 destroy 用不到它：

```bash
terraform destroy -var 'state_bucket_name=medical-tfstate-<account-id>'
```

不带 `-var` 会进入交互式提示。Terraform 对 plan / apply / destroy 都要求每个变量有值。

> **输入的名字不决定删什么**。destroy 删的是 state 里记录的资源，不是你输入的名字对应的
> 资源。所以输错名字不会误删别的桶，但也不会阻止删除。

### 桶非空时 destroy 会失败

桶开了版本控制，且没有设 `force_destroy`：

```text
BucketNotEmpty: The bucket you tried to delete is not empty.
You must delete all versions in the bucket.
```

这是有意的护栏——存着 state 的桶不该被一条命令连内容一起删掉。真要删，先手工清空：

```bash
aws s3api delete-objects --bucket <bucket> \
  --delete "$(aws s3api list-object-versions --bucket <bucket> \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"

aws s3api delete-objects --bucket <bucket> \
  --delete "$(aws s3api list-object-versions --bucket <bucket> \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}')"
```

再重跑 destroy。

## 本地 state 丢了怎么办

不要把 `terraform.tfstate` 提交进仓库。它虽然只管两个资源，但 state 文件是不该进版本库的
一类文件，而且这份很容易重建。

丢了就重新导入：

```bash
terraform init

terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_versioning.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_server_side_encryption_configuration.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_public_access_block.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_dynamodb_table.tf_lock medical-tf-lock
```

导入后跑一次确认没有差异：

```bash
terraform plan -var 'state_bucket_name=<bucket>'
# 预期 No changes
```

## 桶的配置

```text
版本控制    开启，state 可回溯
加密        SSE，静态加密
公网访问    全部阻止
锁表        DynamoDB，防止并发 apply 互相覆盖
```
