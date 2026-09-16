locals {
  upload_environments = toset(["staging", "prod"])
}

# Separate private buckets and roles prevent one environment reading the other.
resource "aws_s3_bucket" "uploads" {
  for_each      = local.upload_environments
  bucket_prefix = "${var.cluster_name}-${each.key}-uploads-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  for_each                = local.upload_environments
  bucket                  = aws_s3_bucket.uploads[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  for_each = local.upload_environments
  bucket   = aws_s3_bucket.uploads[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  for_each = local.upload_environments
  bucket   = aws_s3_bucket.uploads[each.key].id
  rule {
    id     = "expire-abandoned-uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "uploads" {
  for_each = local.upload_environments
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.uploads[each.key].arn}/uploads/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads[each.key].arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["uploads/*"]
    }
  }
}

resource "aws_iam_policy" "uploads" {
  for_each    = local.upload_environments
  name_prefix = "${var.cluster_name}-${each.key}-uploads-"
  policy      = data.aws_iam_policy_document.uploads[each.key].json
}

module "uploads_irsa" {
  for_each  = local.upload_environments
  source    = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version   = "~> 5.0"
  role_name = "${var.cluster_name}-${each.key}-uploads"
  role_policy_arns = {
    uploads = aws_iam_policy.uploads[each.key].arn
  }
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["asyncvtp-${each.key}:asyncvtp-uploads"]
    }
  }
}

output "upload_storage" {
  description = "Set each environment's objectStorage.bucket and serviceAccount.roleArn in Helm values before syncing"
  value = { for env in local.upload_environments : env => {
    bucket   = aws_s3_bucket.uploads[env].id
    role_arn = module.uploads_irsa[env].iam_role_arn
    region   = var.region
  } }
}
