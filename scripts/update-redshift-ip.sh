#!/usr/bin/env bash

set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-612674025488-AdministratorAccess}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-03308b0434d102357}"
PORT="${PORT:-5439}"

CURRENT_IP="$(curl -4 -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')"

if [[ ! "$CURRENT_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "无法获取有效公网 IP: $CURRENT_IP" >&2
  exit 1
fi

CURRENT_CIDR="${CURRENT_IP}/32"
AWS=(aws --profile "$AWS_PROFILE" --region "$AWS_REGION")

echo "当前公网 IP: $CURRENT_IP"
echo "Security Group: $SECURITY_GROUP_ID"
echo "清理旧的 TCP $PORT IPv4 规则..."

RULES="$(${AWS[@]} ec2 describe-security-group-rules \
  --filters "Name=group-id,Values=${SECURITY_GROUP_ID}" \
  --query "SecurityGroupRules[?IsEgress==\`false\` && IpProtocol=='tcp' && FromPort==\`${PORT}\` && ToPort==\`${PORT}\` && CidrIpv4!=null].[SecurityGroupRuleId,CidrIpv4]" \
  --output text)"

if [[ -n "$RULES" ]]; then
  while IFS=$'\t' read -r RULE_ID CIDR; do
    [[ -z "$RULE_ID" ]] && continue

    if [[ "$CIDR" != "$CURRENT_CIDR" ]]; then
      echo "删除旧规则: $CIDR ($RULE_ID)"
      "${AWS[@]}" ec2 revoke-security-group-ingress \
        --group-id "$SECURITY_GROUP_ID" \
        --security-group-rule-ids "$RULE_ID" >/dev/null
    fi
  done <<< "$RULES"
fi

CURRENT_EXISTS="$(${AWS[@]} ec2 describe-security-group-rules \
  --filters "Name=group-id,Values=${SECURITY_GROUP_ID}" \
  --query "SecurityGroupRules[?IsEgress==\`false\` && IpProtocol=='tcp' && FromPort==\`${PORT}\` && ToPort==\`${PORT}\` && CidrIpv4=='${CURRENT_CIDR}'] | length(@)" \
  --output text)"

if [[ "$CURRENT_EXISTS" == "0" ]]; then
  echo "添加当前 IP 白名单: $CURRENT_CIDR"
  "${AWS[@]}" ec2 authorize-security-group-ingress \
    --group-id "$SECURITY_GROUP_ID" \
    --protocol tcp \
    --port "$PORT" \
    --cidr "$CURRENT_CIDR" >/dev/null
else
  echo "当前 IP 已在白名单中: $CURRENT_CIDR"
fi

echo
echo "完成：TCP $PORT 仅允许 $CURRENT_CIDR"
echo "Redshift: medical-poc-wg.612674025488.us-east-1.redshift-serverless.amazonaws.com"
