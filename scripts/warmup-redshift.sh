#!/usr/bin/env bash
# On-demand Redshift Serverless warm-up (方案 A).
#
# WHY: this workgroup goes idle -> auto-suspends -> $0 while unused. The FIRST
# query after idle pays a compute resume/warm-up penalty that we measured at
# 90-510s (independent of query complexity), which makes the MCP agent look
# "stuck" on the first question of a demo. Keeping it warm 24/7 is not an
# option: base is 32 RPU, and the monthly usage limit is only 300 RPU-hours
# (~9.4h of continuous compute) before the workgroup auto-deactivates.
#
# INSTEAD: run this a few minutes BEFORE a demo. It resumes compute and, more
# importantly, forces Redshift to COMPILE the query segments for these shapes
# (Serverless compiles first-time segments via an external code cache; cold
# segments can intermittently take minutes -- this is the real reason a trivial
# query looks "stuck"). Once compiled+cached here, the demo's real queries are
# cache hits (sub-second to a few seconds). After the demo the workgroup goes
# idle again on its own. Cost = just the few minutes of RPU you actually use.
#
# NOTE: the first pass is slow ON PURPOSE (it is doing the compiling). We never
# cancel a warm-up query -- cancelling would throw away the very compilation we
# want cached. If one exceeds MAX_WAIT we simply stop waiting and let it finish
# (and cache) in the background while we move on.
#
# Usage:
#   scripts/warmup-redshift.sh
#   AWS_PROFILE=... WORKGROUP=medical-dev-wg scripts/warmup-redshift.sh
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-612674025488-AdministratorAccess}"
AWS_REGION="${AWS_REGION:-us-east-1}"
WORKGROUP="${WORKGROUP:-medical-dev-wg}"
DATABASE="${DATABASE:-medical_dw}"
# How long to WAIT (not cancel) before moving on to the next warm-up query.
# Cold segment compilation can take several minutes; a statement we stop
# waiting on keeps running on Redshift and still caches its compiled segments.
MAX_WAIT="${MAX_WAIT:-420}"

AWS=(aws --profile "$AWS_PROFILE" --region "$AWS_REGION")

echo "workgroup=$WORKGROUP database=$DATABASE profile=$AWS_PROFILE region=$AWS_REGION"

# Resolve the admin secret from the workgroup's namespace (no hard-coded ARN).
NS="$(${AWS[@]} redshift-serverless get-workgroup \
  --workgroup-name "$WORKGROUP" \
  --query 'workgroup.namespaceName' --output text)"
SECRET_ARN="$(${AWS[@]} redshift-serverless get-namespace \
  --namespace-name "$NS" \
  --query 'namespace.adminPasswordSecretArn' --output text)"

if [[ -z "$SECRET_ARN" || "$SECRET_ARN" == "None" ]]; then
  echo "could not resolve admin secret ARN for namespace '$NS'" >&2
  exit 1
fi

# Representative warm-up queries. #1 (select 1) triggers the compute resume;
# the rest touch each main fact table + a join and the wide ADS table so the
# execution engine and block cache are hot for the demo's real queries.
QUERIES=(
  "select 1"
  "select p.therapy_area, sum(i.net_amount) net_sales
     from dwd.fct_sales_order_item i
     join dwd.dim_product p on i.product_id = p.product_id
     group by 1 order by 2 desc"
  "select r.sales_region_name, sum(i.net_amount) net_sales, count(*) items
     from dwd.fct_sales_order_item i
     join dwd.dim_sales_region r on i.sales_region_id = r.sales_region_id
     group by 1 order by 2 desc"
  "select sum(payment_amount) payments, count(*) n from dwd.fct_payment"
  "select p.therapy_area, sum(x.prescription_count) rx, sum(x.patient_count) patients
     from dwd.fct_prescription x
     join dwd.dim_product p on x.product_id = p.product_id
     group by 1 order by 2 desc"
  "select touchpoint_type, sum(cost_amount) cost from dwd.fct_campaign_touchpoint group by 1 order by 2 desc"
  "select therapy_area, sales_region_name,
          sum(net_sales_amount) net_sales, sum(payment_amount) payments,
          sum(campaign_cost) cost, sum(attributed_revenue) attributed
     from ads.ads_sales_attribution_wide
     group by 1, 2 order by 3 desc"
)

run_stmt() {
  local sql="$1" label="$2" t0 id status err elapsed
  t0=$(date +%s)
  id="$(${AWS[@]} redshift-data execute-statement \
    --workgroup-name "$WORKGROUP" --database "$DATABASE" \
    --secret-arn "$SECRET_ARN" --sql "$sql" --query 'Id' --output text)"
  while true; do
    sleep 3
    status="$(${AWS[@]} redshift-data describe-statement --id "$id" --query 'Status' --output text)"
    case "$status" in
      FINISHED|FAILED|ABORTED) break ;;
    esac
    if (( $(date +%s) - t0 > MAX_WAIT )); then
      # Do NOT cancel: let it finish and cache its compiled segments in the
      # background. We just stop blocking on it.
      status="RUNNING(bg)"; break
    fi
  done
  elapsed=$(( $(date +%s) - t0 ))
  if [[ "$status" == "FAILED" ]]; then
    err="$(${AWS[@]} redshift-data describe-statement --id "$id" --query 'Error' --output text)"
    printf '  %-14s [%s] %ss  %s\n' "$label" "$status" "$elapsed" "$err"
  else
    printf '  %-14s [%s] %ss\n' "$label" "$status" "$elapsed"
  fi
}

echo "warming up (first query resumes compute and may take a few minutes)..."
START=$(date +%s)
i=0
for sql in "${QUERIES[@]}"; do
  i=$((i + 1))
  run_stmt "$sql" "query-$i"
done
echo "done in $(( $(date +%s) - START ))s. Workgroup is hot; run your demo now."
