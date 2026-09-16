# AWS EKS portfolio evidence

This folder contains the visual evidence captured during the EKS deployment demonstration.

| Evidence | What it demonstrates |
| --- | --- |
| `01-argocd-healthy-synced.png` | Argo CD shows both `asyncvtp-prod` and `asyncvtp-staging` as **Healthy** and **Synced**, sourced from the GitHub `main` branch. |
| `02-eks-cluster-active.png` | AWS EKS cluster `piroo` is **Active** in `us-east-1`, with zero cluster-health and node-health issues. |
| `03-eks-node-group.png` | The EKS managed node group is **Active**, uses `t3.2xlarge` workers, and is configured for one to three nodes. |
| `04-grafana-celery-autoscaling.png` | Prometheus worker-count query visibly rises from 2 to 4; its tooltip records a value of **4** Celery workers. |
| `05-video-pipeline-ui.png` | The portfolio application's Video Pipeline Tester interface. This supports the product walkthrough but does not independently prove an end-to-end completed upload. |
| `06-video-task-completed.png` | An uploaded MP4 task is visibly **Completed** and includes an AI-generated summary. This is the end-to-end application proof. |

## Supporting command evidence

The detailed, reproducible deployment evidence is recorded in [EKS verification](../eks-verification.md). It includes the EKS context, running workload status, storage, Argo CD applications, and autoscaling test results.

## Capture guidance

Use the EKS **Overview** page rather than the Compute resource list: the latter may show an IAM authorization warning despite the worker nodes being healthy. For Grafana, retain the query and visible 2-to-4 worker peak in the same frame.
