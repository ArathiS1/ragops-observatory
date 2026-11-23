{{- define "ragops.env" -}}
- name: RAGOPS_DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ .Values.database.existingSecret }}, key: DATABASE_URL } }
- name: RAGOPS_ADMIN_KEY
  valueFrom: { secretKeyRef: { name: {{ .Values.adminSecret }}, key: ADMIN_KEY } }
- name: OPENAI_API_KEY
  valueFrom: { secretKeyRef: { name: {{ .Values.adminSecret }}, key: OPENAI_API_KEY, optional: true } }
- { name: RAGOPS_AUTO_MIGRATE, value: {{ (not .Values.migrations.enabled) | toString | quote }} }
- { name: RAGOPS_JUDGE, value: {{ .Values.judge | quote }} }
- { name: RAGOPS_JUDGE_MODEL, value: {{ .Values.judgeModel | quote }} }
- { name: RAGOPS_JUDGE_BASE_URL, value: {{ .Values.judgeBaseUrl | quote }} }
- { name: RAGOPS_EMBEDDER, value: {{ .Values.embedder | quote }} }
- { name: RAGOPS_VECTOR_BACKEND, value: {{ .Values.vectorBackend | quote }} }
- { name: RAGOPS_REDACTOR, value: {{ .Values.redactor | quote }} }
- { name: RAGOPS_CLICKHOUSE_URL, value: {{ .Values.clickhouseUrl | quote }} }
- { name: RAGOPS_PIPELINE_ALLOWED_HOSTS, value: {{ .Values.pipelineAllowedHosts | quote }} }
{{- with .Values.redisUrl }}
- { name: RAGOPS_REDIS_URL, value: {{ . | quote }} }
{{- end }}
{{- with .Values.publicUrl }}
- { name: RAGOPS_PUBLIC_URL, value: {{ . | quote }} }
{{- end }}
{{- if .Values.sso.enabled }}
- { name: RAGOPS_OIDC_ISSUER, value: {{ required "sso.issuer is required when sso.enabled" .Values.sso.issuer | quote }} }
- { name: RAGOPS_OIDC_CLIENT_ID, value: {{ required "sso.clientId is required when sso.enabled" .Values.sso.clientId | quote }} }
- name: RAGOPS_OIDC_CLIENT_SECRET
  valueFrom: { secretKeyRef: { name: {{ .Values.adminSecret }}, key: OIDC_CLIENT_SECRET, optional: true } }
- { name: RAGOPS_OIDC_ORG, value: {{ .Values.sso.org | quote }} }
- { name: RAGOPS_OIDC_ROLE_CLAIM, value: {{ .Values.sso.roleClaim | quote }} }
- { name: RAGOPS_OIDC_ROLE_MAP, value: {{ .Values.sso.roleMap | quote }} }
- { name: RAGOPS_OIDC_DEFAULT_ROLE, value: {{ .Values.sso.defaultRole | quote }} }
- { name: RAGOPS_OIDC_ALLOWED_DOMAINS, value: {{ .Values.sso.allowedDomains | quote }} }
- { name: RAGOPS_OIDC_PROVIDER_NAME, value: {{ .Values.sso.providerName | quote }} }
{{- end }}
{{- if .Values.smtp.host }}
- { name: RAGOPS_SMTP_HOST, value: {{ .Values.smtp.host | quote }} }
- { name: RAGOPS_SMTP_PORT, value: {{ .Values.smtp.port | quote }} }
- { name: RAGOPS_SMTP_USER, value: {{ .Values.smtp.user | quote }} }
- { name: RAGOPS_SMTP_SECURITY, value: {{ .Values.smtp.security | quote }} }
- { name: RAGOPS_SMTP_FROM, value: {{ .Values.smtp.from | quote }} }
- name: RAGOPS_SMTP_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ .Values.adminSecret }}, key: SMTP_PASSWORD, optional: true } }
{{- end }}
{{- end -}}

{{- define "ragops.labels" -}}
app.kubernetes.io/name: ragops-observatory
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ragops.podSpecCommon" -}}
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
