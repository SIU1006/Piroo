{{/*
Chart name and version label
*/}}
{{- define "asyncvtp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
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
{{- if $registry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) .image.repository .image.tag -}}
{{- else -}}
{{- printf "%s:%s" .image.repository .image.tag -}}
{{- end -}}
{{- end -}}
