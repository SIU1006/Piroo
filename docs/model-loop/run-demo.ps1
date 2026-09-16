param([string]$OutputDirectory = "docs/model-loop/run-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))")
$ErrorActionPreference = 'Stop'
$loopRepo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location $loopRepo
$loopOutput = [IO.Path]::GetFullPath((Join-Path $loopRepo $OutputDirectory))
if (-not $loopOutput.StartsWith($loopRepo + [IO.Path]::DirectorySeparatorChar)) {
    throw 'OutputDirectory must be inside this repository.'
}
if (Test-Path (Join-Path $loopOutput 'registry.db')) {
    throw 'Use a new directory; this example initializes an isolated registry.'
}
New-Item -ItemType Directory -Force $loopOutput | Out-Null
$loopDockerOutput = '/workspace/' + [IO.Path]::GetRelativePath($loopRepo, $loopOutput).Replace('\', '/')
$loopNetwork = 'asyncvtp-loop-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$loopStable = "$loopNetwork-stable"
$loopCandidate = "$loopNetwork-candidate"

function Invoke-LoopDocker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker operation failed: $($DockerArgs[0])" }
}

function Wait-LoopReady {
    param([string]$Container)
    for ($loopAttempt = 0; $loopAttempt -lt 60; $loopAttempt++) {
        & docker exec $Container python -c "import urllib.request; urllib.request.urlopen('http://localhost:3000/readyz', timeout=2)" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "Inference service did not become ready: $Container"
}

function Invoke-LoopPhase {
    param([string]$Phase)
    & docker run --rm --network $loopNetwork --mount "type=bind,source=$loopRepo,target=/workspace" `
        --workdir /workspace -e GIT_PYTHON_REFRESH=quiet asyncvtp-model-eval:6 `
        -m model_eval.lifecycle_demo $Phase --output $loopDockerOutput `
        --baseline-image $loopBaselineImage --candidate-image $loopCandidateImage `
        --stable-endpoint "http://${loopStable}:3000" --candidate-endpoint "http://${loopCandidate}:3000" `
        > (Join-Path $loopOutput "$Phase.log") 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Phase failed; inspect $Phase.log" }
    Write-Host "Completed $Phase"
}

function Save-LoopDeployment {
    param([string]$Filename)
    & docker inspect $loopStable --format '{"container_id":{{json .Id}},"image_id":{{json .Image}},"image_ref":{{json .Config.Image}},"started_at":{{json .State.StartedAt}}}' `
        > (Join-Path $loopOutput $Filename)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect deployed image' }
}

try {
    Invoke-LoopDocker @('build', '-f', 'docs/model-loop/Dockerfile', '-t', 'asyncvtp-model-eval:6', '.')
    foreach ($loopSize in @('tiny', 'base')) {
        Invoke-LoopDocker @('build', '-f', 'Dockerfile.inference', '--build-arg', "WHISPER_MODEL_SIZE=$loopSize", '-t', "asyncvtp-model-loop:$loopSize", '.')
    }
    $loopBaselineImage = (& docker image inspect asyncvtp-model-loop:tiny --format '{{.Id}}').Trim()
    $loopCandidateImage = (& docker image inspect asyncvtp-model-loop:base --format '{{.Id}}').Trim()
    Invoke-LoopDocker @('network', 'create', $loopNetwork)
    Invoke-LoopDocker @('run', '-d', '--name', $loopStable, '--network', $loopNetwork, 'asyncvtp-model-loop:tiny')
    Invoke-LoopDocker @('run', '-d', '--name', $loopCandidate, '--network', $loopNetwork, 'asyncvtp-model-loop:base')
    Wait-LoopReady $loopStable
    Wait-LoopReady $loopCandidate
    Save-LoopDeployment 'initial-deployment.json'
    Invoke-LoopPhase 'evaluate'
    Invoke-LoopPhase 'promote'
    Invoke-LoopDocker @('rm', '-f', $loopStable)
    Invoke-LoopDocker @('run', '-d', '--name', $loopStable, '--network', $loopNetwork, 'asyncvtp-model-loop:base')
    Wait-LoopReady $loopStable
    Save-LoopDeployment 'deployment.json'
    Invoke-LoopPhase 'verify'
    Invoke-LoopPhase 'rollback'
    Invoke-LoopDocker @('rm', '-f', $loopStable)
    Invoke-LoopDocker @('run', '-d', '--name', $loopStable, '--network', $loopNetwork, 'asyncvtp-model-loop:tiny')
    Wait-LoopReady $loopStable
    Save-LoopDeployment 'rollback-deployment.json'
    Invoke-LoopPhase 'verify-rollback'
    Invoke-LoopDocker @('run', '--rm', '--mount', "type=bind,source=$loopRepo,target=/workspace", '--workdir', '/workspace', 'asyncvtp-model-eval:6', '-m', 'model_eval.export_evidence', $loopDockerOutput)
    Write-Host "Evidence preserved in $loopOutput"
} finally {
    # Names are generated for this run; shared deployments are never selected.
    & docker rm -f $loopStable $loopCandidate 2>$null | Out-Null
    & docker network rm $loopNetwork 2>$null | Out-Null
}
