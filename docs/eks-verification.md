# AWS EKS deployment verification

Verified on 16 September 2026 (Australia/Sydney) against AWS account `657830185335`.

## Cluster evidence

| Check | Result |
| --- | --- |
| EKS cluster | `piroo` in `us-east-1` |
| Control plane | `ACTIVE`, Kubernetes `1.31` |
| Worker capacity | One Ready Amazon Linux 2023 node; managed node group `t3.2xlarge`, configured for 1–3 nodes |
| Storage | EBS CSI add-on `ACTIVE`; custom `gp3` StorageClass available; all eight application PVCs `Bound` |
| GitOps | Argo CD running; `asyncvtp-staging` and `asyncvtp-prod` both `Synced` and `Healthy` |
| Applications | FastAPI, Celery, Whisper, Redis, MLflow, Ollama, Prometheus, Grafana, Alertmanager, and supporting Kubernetes services running across staging and production namespaces |
| Autoscaling | Celery HPAs active and reporting CPU metrics |

The infrastructure was provisioned from the Terraform configuration in `terraform/` and applications were reconciled by the Argo CD ApplicationSet in `argocd/Applicationset.yaml`.

## Reproduce the checks

```powershell
aws eks describe-cluster --region us-east-1 --name piroo
aws eks update-kubeconfig --region us-east-1 --name piroo
kubectl get nodes
kubectl get applications.argoproj.io -A
kubectl get pods -A
kubectl get storageclass,pvc -A
```

> This deployment is intentionally short-lived for portfolio demonstration and should be torn down with Terraform after the evidence has been captured to avoid ongoing AWS charges.
