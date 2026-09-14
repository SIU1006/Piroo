variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "piroo"
}

variable "cluster_version" {
  description = "Kubernetes version (must be in EKS standard support)"
  type        = string
  default     = "1.31"
}

variable "instance_type" {
  description = "EC2 instance type for the managed node group (4 vCPU / 16 GiB fits staging + prod)"
  type        = string
  default     = "t3.xlarge"
}

variable "node_desired" {
  description = "Desired number of worker nodes"
  type        = number
  default     = 1
}

variable "node_min" {
  description = "Minimum number of worker nodes"
  type        = number
  default     = 1
}

variable "node_max" {
  description = "Maximum number of worker nodes"
  type        = number
  default     = 3
}
