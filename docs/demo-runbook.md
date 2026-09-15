# EKS Demo Runbook

One focused session (~2 hrs) to provision EKS, capture evidence, and tear down.
Both environments (staging + prod) plus the live app demo. Cost ≈ $0.50–0.75 total
if done in one sitting; the risk is *leaving it running*.

> Restart your terminal after installing tools so `terraform` and `aws` are on PATH.

---

## 0. One-time setup (before starting the timer)

```bash
# authenticate (Free plan account) + set region
aws configure            # or: aws sso login
aws sts get-caller-identity   # sanity check

cd terraform
terraform init           # providers/modules (already lock-filed)
```

## 1. Provision (~15–20 min — start the timer)

```bash
cd terraform
terraform plan           # FREE — catch errors first, save output for evidence
terraform apply -auto-approve
```

While it provisions, open a scratch buffer and stage the next commands.

## 2. Wire up access

```bash
terraform output configure_kubectl        # -> aws eks update-kubeconfig ... ; run it
terraform output argocd_admin_password    # -> kubectl ... | base64 -d ; run it
```

```bash
# ArgoCD UI (https://localhost:8080, user admin)
kubectl port-forward -n argocd svc/argocd-server 8080:443
```

## 2b. Apply the ApplicationSet (hands GitOps off to ArgoCD)

The ApplicationSet isn't managed by Terraform (its CRD is installed by the
ArgoCD release, so referencing it in the same run breaks `terraform plan`).

```bash
kubectl apply -f ../argocd/Applicationset.yaml
```

ArgoCD now creates and reconciles `asyncvtp-staging` and `asyncvtp-prod`.

## 3. Secrets + adapter cert (create BEFORE ArgoCD syncs the apps)

```powershell
foreach ($ns in @("asyncvtp-staging","asyncvtp-prod")) {
  kubectl -n $ns create secret generic redis-secret      --from-literal=redis-password='demo-redis'
  kubectl -n $ns create secret generic grafana-secret    --from-literal=admin-password='demo-grafana'
  kubectl -n $ns create secret generic alertmanager-secret --from-literal=slack-webhook-url='https://hooks.slack.com/services/REPLACE/ME'
}
```

```powershell
$openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
foreach ($ns in @("asyncvtp-staging","asyncvtp-prod")) {
  $cn = "prometheus-adapter-service.$ns.svc"
  & $openssl req -x509 -newkey rsa:2048 -nodes -keyout serving.key -out serving.crt -days 3650 `
    -subj "/CN=$cn" -addext "subjectAltName=DNS:$cn,DNS:$cn.cluster.local"
  kubectl -n $ns create secret generic cm-adapter-serving-certs `
    --from-file=serving.crt=serving.crt --from-file=serving.key=serving.key `
    --from-file=apiserver.crt=serving.crt --from-file=apiserver.key=serving.key
}
```

## 4. Kick off the ollama model pull EARLY (slowest step)

```bash
kubectl exec -n asyncvtp-staging deploy/ollama -- ollama pull llama3.2
kubectl exec -n asyncvtp-prod     deploy/ollama -- ollama pull llama3.2
```

Run both, then let them finish in the background while you capture other evidence.

## 5. Wait for Healthy

```bash
kubectl -n argocd get applications --watch     # until both = Synced + Healthy
kubectl -n asyncvtp-staging get pods           # all Running
```

## 6. Capture evidence (one pass)

- [ ] `terraform apply` output ("Apply complete! Resources: N added")
- [ ] `terraform output` results
- [ ] AWS console: EKS cluster, EC2 node, VPC
- [ ] ArgoCD UI: `asyncvtp-staging` + `asyncvtp-prod` **Synced + Healthy**
- [ ] Grafana dashboard: `kubectl port-forward -n asyncvtp-staging svc/grafana 3000:3000`
- [ ] Live app: `kubectl port-forward -n asyncvtp-staging svc/fastapi-service 8000:8000`
      → upload a video → Whisper transcript → Ollama summary
- [ ] `kubectl -n asyncvtp-staging get pods` (all Running)

## 7. Tear down IMMEDIATELY (order matters — EBS orphans otherwise)

```bash
kubectl delete pvc --all -n asyncvtp-staging
kubectl delete pvc --all -n asyncvtp-prod

cd terraform
terraform destroy -auto-approve
```

Then verify **$0** lingering:

```bash
aws ec2 describe-volumes --region us-east-1 --filters Name=status,Values=available
aws elbv2 describe-load-balancers --region us-east-1
```

Both must be empty. Check your billing alarm didn't fire, then commit the evidence.

---

### If something fails and you need to pause

- The control plane is the fixed cost ($0.10/hr). If you're not actively working,
  **`terraform destroy` now** and re-`apply` when ready — don't leave it overnight.
