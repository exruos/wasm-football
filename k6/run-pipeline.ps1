param (
    [Parameter(Mandatory = $false)]
    [ValidateSet("wasm-rust", "wasm-js", "oci-axum", "oci-node", "oci-spring")]
    [string]$Target,

    [Parameter(Mandatory = $false)]
    [int]$BaselineReplays = 10,

    [Parameter(Mandatory = $false)]
    [int]$ColdstartReplays = 30,

    [Parameter(Mandatory = $false)]
    [int]$ScalingReplays = 10
)

# --- START LOGGING ---
$LogFile = ".\.output\pipeline_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $LogFile -Append
# ---------------------

$ErrorActionPreference = "Stop"
$EnvBaseDir = "..\kustomize\environments"

$TargetMap = @{
    "wasm-rust"  = @{ Baseline = "wasm-rust-baseline"; Scaled = "wasm-rust-scaled" }
    "wasm-js"    = @{ Baseline = "wasm-js-baseline"; Scaled = "wasm-js-scaled" }
    "oci-axum"   = @{ Baseline = "axum-baseline"; Scaled = "axum-scaled" }
    "oci-node"   = @{ Baseline = "node-baseline"; Scaled = "node-scaled" }
    "oci-spring" = @{ Baseline = "spring-baseline"; Scaled = "spring-scaled" }
}

$TargetsToRun = if ($null -ne $Target) { @($Target) } else { $TargetMap.Keys }

try {
    foreach ($CurrentTarget in $TargetsToRun) {
        
        $Mapping = $TargetMap[$CurrentTarget]
        $BaselineEnvPath = Join-Path $EnvBaseDir $Mapping.Baseline
        $ScaledEnvPath = Join-Path $EnvBaseDir $Mapping.Scaled

        Write-Host "`n=======================================================" -ForegroundColor Cyan
        Write-Host "🚀 Starting Benchmarking Pipeline for Target: $CurrentTarget..." -ForegroundColor Cyan
        Write-Host "=======================================================" -ForegroundColor Cyan

        # Clear namespace before applying new configurations
        kubectl delete namespace football --ignore-not-found

        # -------------------------------------------------------------
        # Phase 1: Baseline Scenario
        # -------------------------------------------------------------
        Write-Host "`n[1/3] Deploying Baseline Environment ($BaselineEnvPath)..." -ForegroundColor Yellow
        kubectl apply -k $BaselineEnvPath
        
        # Custom conditional double-apply logic for WASM targets
        if ($CurrentTarget -eq "wasm-rust" -or $CurrentTarget -eq "wasm-js") {
            kubectl apply -k $BaselineEnvPath
        }
        Start-Sleep -Seconds 15

        Write-Host "Running baseline scenario ($BaselineReplays replays)..." -ForegroundColor Magenta
        .\benchmark.ps1 -Target $CurrentTarget -Scenario "baseline" -Replays $BaselineReplays

        # -------------------------------------------------------------
        # Phase 2: Coldstart Scenario (on scaled env)
        # -------------------------------------------------------------
        Write-Host "`n[2/3] Deploying Scaled Environment ($ScaledEnvPath)..." -ForegroundColor Yellow
        kubectl apply -k $ScaledEnvPath
        Start-Sleep -Seconds 15

        Write-Host "Running coldstart scenario ($ColdstartReplays replays)..." -ForegroundColor Magenta
        .\benchmark.ps1 -Target $CurrentTarget -Scenario "coldstart" -Replays $ColdstartReplays

        # -------------------------------------------------------------
        # Phase 3: Scaling Scenario (on scaled env)
        # -------------------------------------------------------------
        Write-Host "`n[3/3] Running scaling scenario ($ScalingReplays replays)..." -ForegroundColor Magenta
        .\benchmark.ps1 -Target $CurrentTarget -Scenario "scaling" -Replays $ScalingReplays
    }

    Write-Host "`n✅ All selected pipelines completed successfully!" -ForegroundColor Green
}
finally {
    Stop-Transcript
}