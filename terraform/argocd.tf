resource "kubernetes_storage_class" "gp3" {
  metadata {
    name = "gp3"
  }

  storage_provisioner    = "ebs.csi.aws.com"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true

  parameters = {
    type = "gp3"
  }

  depends_on = [aws_eks_access_policy_association.admin]
}

# install argocd as helm chart
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true

  depends_on = [aws_eks_access_policy_association.admin]

  # Pin a version for reproducibility, e.g. version = "7.8.15"
}

# The ApplicationSet is deliberately NOT a kubernetes_manifest here: its CRD is
# installed by the ArgoCD release above, so referencing it in the same run makes
# terraform plan fail with "CRD may not be installed". Apply it with kubectl
# right after terraform apply instead (see docs/demo-runbook.md):
#   kubectl apply -f ../argocd/Applicationset.yaml
