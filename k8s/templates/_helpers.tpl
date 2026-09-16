{{/*
Chart name and version label
*/}}
{{- define "asyncvtp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* External Secrets require an explicit version bump on rotation (also in GitOps). */}}
{{- define "asyncvtp.objectStorageSecretChecksum" -}}
{{- dict "credentialsSecret" .Values.objectStorage.credentialsSecret "credentialsVersion" .Values.objectStorage.credentialsSecretVersion "caSecret" .Values.objectStorage.caSecret "caVersion" .Values.objectStorage.caSecretVersion | toJson | sha256sum -}}
{{- end -}}

{{- define "asyncvtp.objectStorageCaMount" -}}
{{- if .Values.objectStorage.caSecret -}}
- name: object-storage-ca
  mountPath: /etc/asyncvtp/object-storage-ca
  readOnly: true
{{- end -}}
{{- end -}}

{{- define "asyncvtp.objectStorageCaVolume" -}}
{{- if .Values.objectStorage.caSecret -}}
- name: object-storage-ca
  secret:
    secretName: {{ .Values.objectStorage.caSecret }}
    items:
    - key: ca.crt
      path: ca.crt
{{- end -}}
{{- end -}}

{{/*
Rendered Alertmanager configuration, shared by the ConfigMap and pod checksum.
*/}}
{{- define "asyncvtp.alertmanagerConfig" -}}
global:
  resolve_timeout: 5m

route:
  receiver: {{ ternary "slack" "local" .Values.alertmanager.notifications.enabled | quote }}
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'local'
{{ if .Values.alertmanager.notifications.enabled }}
  - name: 'slack'
    slack_configs:
      - api_url_file: /etc/alertmanager/secrets/slack-webhook-url
        channel: {{ .Values.alertmanager.slackChannel | quote }}
        send_resolved: true
        title: {{`'{{ .CommonAnnotations.summary }}'`}}
        text: {{`'{{ .CommonAnnotations.description }}'`}}
{{ end }}
{{- end -}}

{{/*
Common labels applied to every resource.
*/}}
{{- define "asyncvtp.labels" -}}
helm.sh/chart: {{ include "asyncvtp.chart" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Resolve an image ref from a repository/tag map, honoring global.imageRegistry
for app-owned images. Usage: {{ include "asyncvtp.image" .Values.fastapi.image }}
Call as: {{ include "asyncvtp.image" (dict "image" .Values.fastapi.image "root" $) }}
*/}}
{{- define "asyncvtp.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- if .image.digest -}}
{{- if $registry -}}
{{- printf "%s/%s@%s" (trimSuffix "/" $registry) .image.repository .image.digest -}}
{{- else -}}
{{- printf "%s@%s" .image.repository .image.digest -}}
{{- end -}}
{{- else -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) .image.repository .image.tag -}}
{{- else -}}
{{- printf "%s:%s" .image.repository .image.tag -}}
{{- end -}}
{{- end -}}
{{- end -}}
