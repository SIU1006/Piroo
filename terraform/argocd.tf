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
}

# install argocd as helm chart
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true

  # Pin a version for reproducibility, e.g. version = "7.8.15"
}


resource "kubernetes_manifest" "applicationset" {
  manifest   = yamldecode(file("${path.module}/../argocd/Applicationset.yaml"))
  depends_on = [helm_release.argocd]
}
