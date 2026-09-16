# Terraform — EKS Infrastructure

Provision the AWS EKS cluster, bootstrap ArgoCD, and hand the rest off to GitOps.

## What this manages

| Layer | Tool |
|---|---|
| VPC, subnets | Terraform (`terraform-aws-modules/vpc`) |
| EKS control plane + node group | Terraform (`terraform-aws-modules/eks`) |
| EBS CSI driver (StorageClass) | Terraform (EKS addon + `gp3` SC) |
| Private upload buckets, lifecycle and per-environment IRSA roles | Terraform (`uploads.tf`) |
| ArgoCD | Terraform (`helm_release`) |
| ApplicationSet (staging + prod) | `kubectl apply` after `terraform apply` (see demo-runbook) |
| Apps (fastapi, celery, whisper, ...) | **ArgoCD** (GitOps, from `main`) |

`terraform apply` gives you: cluster → ArgoCD. Then `kubectl apply -f ../argocd/Applicationset.yaml`
hands off to GitOps: staging + prod auto-deploy from the repo.

> The ApplicationSet is deliberately not a `kubernetes_manifest` — its CRD is
> installed by the ArgoCD release in the same run, which breaks `terraform plan`.

## Prerequisites

- AWS account (Free plan works — $200 credit / 6 months)
- `aws` CLI + `terraform` (~1.5+)
- Configure credentials: `aws configure`

## Usage

```bash
cd terraform
terraform init
terraform plan                 # review, and keep the output as evidence
terraform apply                # ~15-20 min (EKS provisioning)
```

Then:

```bash
terraform output configure_kubectl   # -> aws eks update-kubeconfig ...
terraform output argocd_admin_password  # -> kubectl ... | base64 -d
```

Create the app secrets (out-of-band, not in Terraform — see `k8s/setup-secrets.ps1`):

```bash
kubectl create namespace asyncvtp-staging
kubectl create namespace asyncvtp-prod
# run k8s/setup-secrets.ps1 for each namespace, plus the cm-adapter-serving-certs secret
```

## Required upload configuration for EKS

Run `terraform output -json upload_storage`. Set `objectStorage.bucket`,
`objectStorage.region` and `objectStorage.serviceAccount.roleArn` in each
environment's values file from its output, then commit those non-secret values
before Argo CD sync. Empty buckets intentionally fail chart rendering.
AWS credentials come from IRSA; do not add static keys to Helm values.

Redis, MLflow and Ollama retain separate single-replica `gp3` volumes. Uploads
use S3, and API/workers mount private scratch `emptyDir` volumes. Follow the
[deployment limits and migration guide](../docs/deployment-limits.md) before
upgrading an existing filesystem-based installation. Only production owns the
shared external-metrics adapter and needs its serving-certificate Secret.

## Remote state (optional, production-grade)

Local state is the default so `terraform init` works out of the box. To use S3 +
DynamoDB locking, create the resources once and add a backend block:

```bash
aws s3api create-bucket --bucket piroo-terraform-state --region us-east-1
aws dynamodb create-table --table-name piroo-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

```hcl
terraform {
  backend "s3" {
    bucket         = "piroo-terraform-state"
    key            = "eks/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "piroo-terraform-locks"
  }
}
```

## Tear down (do this promptly on the free tier)

```bash
cd terraform
terraform destroy
# Nonempty upload buckets deliberately block deletion. Export any needed data
# and explicitly empty the demo buckets before retrying destruction.
# verify nothing is left billing you:
aws ec2 describe-volumes --region us-east-1 --filters Name=status,Values=available
aws elbv2 describe-load-balancers --region us-east-1
```
