#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./k8s/setup-secrets.sh --namespace NAMESPACE --values VALUES_FILE

Creates the Redis and Grafana Secrets described by the Helm chart. A Slack
webhook is optional. VALUES_FILE may be an absolute path or a filename inside
k8s/, such as values-staging.yaml.

For non-interactive use, set REDIS_PASSWORD, GRAFANA_ADMIN_PASSWORD, and
optionally SLACK_WEBHOOK_URL in the environment.
EOF
}

namespace=""
values_file=""
while (($#)); do
  case "$1" in
    -n|--namespace) namespace="${2-}"; shift 2 ;;
    -f|--values) values_file="${2-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$namespace" || -z "$values_file" ]]; then
  usage >&2
  exit 2
fi

for command_name in helm kubectl; do
  command -v "$command_name" >/dev/null || {
    echo "Required command not found: $command_name" >&2
    exit 1
  }
done

chart_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$values_file" ]]; then
  values_path="$values_file"
elif [[ -f "$chart_dir/$values_file" ]]; then
  values_path="$chart_dir/$values_file"
else
  echo "Values file not found: $values_file" >&2
  exit 1
fi

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

kubectl get namespace "$namespace" >/dev/null 2>&1 || kubectl create namespace "$namespace"

read_value() {
  local variable_name="$1" prompt="$2" optional="$3" value=""
  value="${!variable_name-}"
  if [[ -z "$value" && -t 0 ]]; then
    read -r -s -p "$prompt: " value
    printf '\n' >&2
  fi
  if [[ -z "$value" && "$optional" != true ]]; then
    echo "$variable_name must not be empty" >&2
    exit 1
  fi
  printf '%s' "$value"
}

render_and_apply() {
  local secret_key="$1" value="$2" value_file
  value_file="$temporary_dir/$secret_key"
  printf '%s' "$value" >"$value_file"
  helm template asyncvtp "$chart_dir" \
    --namespace "$namespace" \
    -f "$values_path" \
    --set secrets.redis.create=false \
    --set secrets.grafana.create=false \
    --set secrets.alertmanager.create=false \
    --set "secrets.$secret_key.create=true" \
    --set-file "secrets.$secret_key.value=$value_file" \
    --show-only templates/secrets.yaml |
    kubectl apply --namespace "$namespace" -f -
}

redis_password="$(read_value REDIS_PASSWORD 'Redis password' false)"
render_and_apply redis "$redis_password"

grafana_password="$(read_value GRAFANA_ADMIN_PASSWORD 'Grafana admin password' false)"
render_and_apply grafana "$grafana_password"

slack_webhook="$(read_value SLACK_WEBHOOK_URL 'Slack Incoming Webhook URL (blank to skip)' true)"
if [[ -n "$slack_webhook" ]]; then
  render_and_apply alertmanager "$slack_webhook"
else
  echo "Skipped alertmanager-secret."
fi

echo "Secrets are ready in namespace $namespace."
