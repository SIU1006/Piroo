{{- define "asyncvtp.adapterConfig" -}}
externalRules:
  - seriesQuery: '{__name__="redis_key_size",key="celery"}'
    resources:
      overrides:
        namespace:
          resource: namespace
    name:
      matches: "redis_key_size"
      as: "celery_queue_depth"
    metricsQuery: 'max by (namespace) (redis_key_size{key="celery",<<.LabelMatchers>>})'
{{- end -}}
