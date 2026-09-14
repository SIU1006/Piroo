# Terraform — EKS Infrastructure

Provision the AWS EKS cluster, bootstrap ArgoCD, and hand the rest off to GitOps.

## What this manages

| Layer | Tool |
|---|---|
| VPC, subnets | Terraform (`terraform-aws-modules/vpc`) |
| EKS control plane + node group | Terraform (`terraform-aws-modules/eks`) |
| EBS CSI driver (StorageClass) | Terraform (EKS addon + `gp3` SC) |
| ArgoCD | Terraform (`helm_release`) |
| ApplicationSet (staging + prod) | Terraform (`kubernetes_manifest`) |
| Apps (fastapi, celery, whisper, ...) | **ArgoCD** (GitOps, from `main`) |

`terraform apply` gives you: cluster → ArgoCD → ApplicationSet → staging+prod auto-deploy.

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

## Required chart change for EKS

`k8s/values-staging.yaml` and `k8s/values-prod.yaml` currently pin
`persistence.*.storageClass: "standard"` (the kind/local-path class). On EKS that
class doesn't exist, so change it before deploying:

- **`"gp3"`** — use the `gp3` SC created by this module (recommended, cheaper/faster), or
- **`""`** (empty) — use the cluster's default (`gp2`).

> `storageClassName` is immutable on PVCs, so make this change on a fresh cluster
> (before the first ArgoCD sync), not as a live edit.

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
# verify nothing is left billing you:
aws ec2 describe-volumes --region us-east-1 --filters Name=status,Values=available
aws elbv2 describe-load-balancers --region us-east-1
```
