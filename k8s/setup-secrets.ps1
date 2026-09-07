<#
.SYNOPSIS
Creates/updates the real in-cluster Secrets that k8s/templates/secrets.yaml
deliberately does NOT create when secrets.<name>.create is false
(staging/prod) - see that template's own comment for why.

Renders the real secrets.yaml template with create forced to $true and the
real value injected via --set, rather than hardcoding each Secret's
name/key here - the chart stays the one source of truth for those.

Nothing here is ever written to a file on disk, so there's nothing for
`git add` to accidentally pick up.

Note: password prompts are NOT masked. Read-Host -AsSecureString doesn't
reliably read piped/non-interactive input (confirmed - it silently hangs),
and the value has to be converted straight back to plaintext anyway to pass
to `helm --set`, which already gives up most of SecureString's benefit. If
you want masking badly enough to accept that tradeoff, swap Read-Host calls
back to `-AsSecureString` + the Marshal conversion - just verify it in your
actual terminal first.

.EXAMPLE
.\k8s\setup-secrets.ps1 -Namespace asyncvtp-staging -ValuesFile values-staging.yaml
.EXAMPLE
.\k8s\setup-secrets.ps1 -Namespace asyncvtp-prod -ValuesFile values-prod.yaml
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Namespace,

    [Parameter(Mandatory = $true)]
    [string]$ValuesFile
)

$ErrorActionPreference = "Stop"
$ChartDir = $PSScriptRoot

function Invoke-RenderAndApply {
    param(
        [string]$SecretKey,
        [string]$Value
    )

    $rendered = helm template asyncvtp $ChartDir `
        -f "$ChartDir/values.yaml" -f "$ChartDir/$ValuesFile" `
        --set "secrets.$SecretKey.create=true" `
        --set "secrets.$SecretKey.value=$Value" `
        --show-only templates/secrets.yaml

    if ($LASTEXITCODE -ne 0) {
        throw "helm template failed for secrets.$SecretKey (exit $LASTEXITCODE)"
    }

    $rendered | kubectl apply --namespace $Namespace -f -

    if ($LASTEXITCODE -ne 0) {
        throw "kubectl apply failed for secrets.$SecretKey (exit $LASTEXITCODE)"
    }
}

$redisPassword = Read-Host -Prompt "Redis password"
Invoke-RenderAndApply -SecretKey "redis" -Value $redisPassword

$grafanaPassword = Read-Host -Prompt "Grafana admin password"
Invoke-RenderAndApply -SecretKey "grafana" -Value $grafanaPassword

$slackWebhookUrl = Read-Host "Slack Incoming Webhook URL (blank to skip Alertmanager secret)"
if ([string]::IsNullOrWhiteSpace($slackWebhookUrl)) {
    Write-Host "Skipped alertmanager-secret - Alertmanager will run with no webhook until you set one."
} else {
    Invoke-RenderAndApply -SecretKey "alertmanager" -Value $slackWebhookUrl
}

Write-Host ""
Write-Host "Done. If pods were already crash-looping on the missing secret, they should recover on their next restart:"
Write-Host "  kubectl get pods -n $Namespace --watch"
