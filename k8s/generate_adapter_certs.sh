#!/usr/bin/env bash

# This script generates a self-signed CA + server cert for prometheus-adapter-service.default.svc, stores the cert/key as a k8s. Secret the Deployment already expects (cm-adapter-serving-certs), and patches the APIService's caBundle so the API server trusts it.

# Run this AFTER `kubectl apply -f k8s/prometheus-adapter.yml` and `k8s/prometheus-adapter-rbac.yml` in order.

# Usage:
#  bash /k8s/generate_adapter_certs.sh

set -euo pipefail

NAMESPACE="default"
SERVICE="prometheus-adapter-service"
SECRET_NAME="cm-adapter-serving-certs"
WORKDIR="$(mktemp -d)"

echo "Generating self-signed CA + server cert in $WORKDIR ..."

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$WORKDIR/ca.key" -out "$WORKDIR/ca.crt" \
  -subj "/CN=prometheus-adapter-ca"

openssl req -newkey rsa:2048 -nodes \
  -keyout "$WORKDIR/tls.key" -out "$WORKDIR/server.csr" \
  -subj "/CN=${SERVICE}.${NAMESPACE}.svc"

cat > "$WORKDIR/san.cnf" <<EOF
subjectAltName = DNS:${SERVICE}.${NAMESPACE}.svc,DNS:${SERVICE}.${NAMESPACE}.svc.cluster.local
EOF

openssl x509 -req -in "$WORKDIR/server.csr" \
  -CA "$WORKDIR/ca.crt" -CAkey "$WORKDIR/ca.key" -CAcreateserial \
  -out "$WORKDIR/tls.crt" -days 3650 -extfile "$WORKDIR/san.cnf"

echo "Creating/updating Secret ${SECRET_NAME} ..."
kubectl create secret tls "$SECRET_NAME" \
  --namespace "$NAMESPACE" \
  --cert="$WORKDIR/tls.crt" --key="$WORKDIR/tls.key" \
  --dry-run=client -o yaml | kubectl apply -f -

CA_BUNDLE_B64="$(base64 -w0 "$WORKDIR/ca.crt" 2>/dev/null || base64 "$WORKDIR/ca.crt")"

echo "Patching APIService v1beta1.external.metrics.k8s.io with the new caBundle ..."
kubectl patch apiservice v1beta1.external.metrics.k8s.io \
  --type=merge \
  -p "{\"spec\":{\"caBundle\":\"${CA_BUNDLE_B64}\"}}"

echo "Restarting prometheus-adapter to pick up the new cert ..."
kubectl rollout restart deployment/prometheus-adapter --namespace "$NAMESPACE"

rm -rf "$WORKDIR"
echo "Done. Verify with:"
echo "  kubectl get apiservice v1beta1.external.metrics.k8s.io"
echo "  kubectl get --raw '/apis/external.metrics.k8s.io/v1beta1/namespaces/default/celery_queue_depth'"