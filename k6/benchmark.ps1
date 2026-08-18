param (
    [Parameter(Mandatory = $true)]
    [ValidateSet("wasm-rust", "wasm-js", "oci-axum", "oci-node", "oci-spring", "oci-native", "wasm-rust-components")]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidateSet("idle", "baseline", "coldstart", "scaling")]
    [string]$Scenario,

    [int]$Replays = 5,

    # The name of the K8s deployment to monitor during cold starts
    [string]$DeploymentName = "football-app",
    [string]$Namespace = "football",

    [string]$VictoriaMetricsUrl = "http://hetzner-vm:8428"
)

# --- Extract metadata from the Target parameter ---
$Parts = $Target -Split '-', 2
$Runtime = $Parts[0]   # "wasm" or "oci"
$Framework = $Parts[1]   # "rust", "js", "axum", "node", or "spring"

# --- Global Configurations ---
$PrometheusWriteUrl = "$VictoriaMetricsUrl/api/v1/write"
$env:K6_PROMETHEUS_RW_SERVER_URL = $PrometheusWriteUrl
$env:K6_FEATURES = "native-histograms"
$ScriptName = "load-test.js"

$scaledObjectName = "football-app-scaler"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " BENCHMARK PROFILE" -ForegroundColor Cyan
Write-Host " Target:   $Runtime ($Framework)"
Write-Host " Scenario: $Scenario"
Write-Host " Replays:  $Replays loop(s)"
Write-Host "=========================================================" -ForegroundColor Cyan

try {
    if ($Scenario -ne "coldstart" -and $Scenario -ne "idle") {
        Write-Host "`n[Warmup] Executing a warmup run to stabilize the target..." -ForegroundColor Yellow
        k6 run --env scenario="warmup" $ScriptName
    }
    else {
        Write-Host "Fetching current KEDA configuration..."
        $originalCooldown = kubectl get scaledobject $scaledObjectName -n football -o jsonpath='{.spec.cooldownPeriod}'
        if ([string]::IsNullOrEmpty($originalCooldown)) { 
            Write-Host "Could not retrieve cooldownPeriod from KEDA. Defaulting to 300 seconds." -ForegroundColor Yellow    
            $originalCooldown = 300
        }
        Write-Host "Original cooldown period is: $originalCooldown seconds"

        Write-Host "Setting cooldownPeriod to 20..."
        kubectl patch scaledobject $scaledObjectName -n football --type='merge' -p '{"spec":{"cooldownPeriod":20}}'
    }

    # --- Safe execution loop ---
    for ($i = 1; $i -le $Replays; $i++) {

        # --- COLDSTART PREPARATION WORKFLOW ---
        if ($Scenario -eq "coldstart") {
            Write-Host "`n[Coldstart Setup] Waiting for KEDA to scale target down to 0..." -ForegroundColor Magenta
        
            # 1. Dynamically wait until KEDA kills all active pods naturally
            while ($true) {
                if ($Runtime -eq "wasm") {
                    # For SpinKube, we check the ready replicas directly on the SpinApp resource
                    $ReadyReplicas = kubectl get spinapp $DeploymentName -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>$null
                    # If the string is empty or '0', KEDA has completely scaled it down
                    $IsScaledDown = [string]::IsNullOrEmpty($ReadyReplicas) -or $ReadyReplicas -eq "0"
                }
                else {
                    # For OCI, we check the standard deployment's ready replicas
                    $ReadyReplicas = kubectl get deployment $DeploymentName -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>$null
                    $IsScaledDown = [string]::IsNullOrEmpty($ReadyReplicas) -or $ReadyReplicas -eq "0"
                }
            
                if ($IsScaledDown) {
                    Write-Host "`n[Coldstart Setup] Target '$DeploymentName' successfully scaled to 0 by KEDA." -ForegroundColor Green
                    Start-Sleep -Seconds 5
                    break
                }
                Write-Host "." -NoNewline -ForegroundColor Gray
                Start-Sleep -Seconds 5
            }
        
            # 2. Energy Cooldown Window
            if ($i -gt 1) {
                Write-Host "[Coldstart Setup] Holding for a 30-second energy cooling window..." -ForegroundColor Yellow
                Start-Sleep -Seconds 30
            }
        }
        elseif ($Scenario -eq "scaling") {
            Write-Host "`n[Scaling Setup] Ensuring target is at one pod scaled before the next replay..." -ForegroundColor Magenta
            while ($true) {
                Write-Host "[Scaling Setup] Triggering a single request to wake up the target..." -ForegroundColor Yellow
                k6 run -q --summary-mode=disabled --env scenario="coldstart" $ScriptName
                if ($Runtime -eq "wasm") {
                    $ReadyReplicas = kubectl get spinapp $DeploymentName -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>$null
                    $IsFullyScaled = -not [string]::IsNullOrEmpty($ReadyReplicas) -and $ReadyReplicas -eq 1
                }
                else {
                    $ReadyReplicas = kubectl get deployment $DeploymentName -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>$null
                    $IsFullyScaled = -not [string]::IsNullOrEmpty($ReadyReplicas) -and $ReadyReplicas -eq 1
                }
            
                if ($IsFullyScaled) {
                    Write-Host "`n[Scaling Setup] Target '$DeploymentName' is at one pod scaled and ready." -ForegroundColor Green
                    break
                }
                Write-Host "." -NoNewline -ForegroundColor Gray
                Start-Sleep -Seconds 10
            }
        }

        # --- BENCHMARK EXECUTION ---
        Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Replay $i of $Replays ..." -ForegroundColor Green

        # 1. CAPTURE EXACT START TIME (RFC3339 format required by VictoriaMetrics)
        $StartTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

        # 2. RUN BENCHMARK OR IDLE WAIT
        if ($Scenario -eq "idle") {
            $IdleMinutes = 10
            Write-Host "Scenario is 'idle'. Waiting for $IdleMinutes minutes to measure idle energy..." -ForegroundColor Cyan

            Start-Sleep -Seconds ($IdleMinutes * 60)
        }
        else {
            k6 run `
                -o experimental-prometheus-rw `
                --env scenario=$Scenario `
                $ScriptName
        }

        # 3. CAPTURE EXACT END TIME
        $EndTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

        # 4. RECORD RUN METADATA TO JSONL
        Write-Host "[Exporting Data] Appending run metadata to JSONL registry..." -ForegroundColor Cyan

        $RunMetadata = [ordered]@{
            StartTime = $StartTime
            EndTime   = $EndTime
            Runtime   = $Runtime
            Framework = $Framework
            Scenario  = $Scenario
            Iteration = $i
        }

        # Ensure output directory exists
        $OutputDir = ".\.output"
        if (-not (Test-Path $OutputDir)) { 
            New-Item -ItemType Directory -Path $OutputDir | Out-Null 
        }

        $JsonlFilePath = "$OutputDir\benchmark_runs.jsonl"

        # Convert to a single-line JSON string (-Compress) and append to the file
        $JsonLine = $RunMetadata | ConvertTo-Json -Compress
        Add-Content -Path $JsonlFilePath -Value $JsonLine -Encoding UTF8

        Write-Host "[Exporting Data] Run metadata appended to $JsonlFilePath" -ForegroundColor Green

        # --- POST-RUN COOLDOWN ---
        if ($i -lt $Replays) {
            if ($Scenario -eq "coldstart") {
                Write-Host "Replay $i finished. Waiting for KEDA's cooldownPeriod for the next cycle..." -ForegroundColor Yellow
                Start-Sleep -Seconds 5
            }
            else {
                Write-Host "Replay $i finished. Waiting 30s for cluster stabilization..." -ForegroundColor Yellow
                Start-Sleep -Seconds 30
            }
        }
    }
}
finally {
    if ($Scenario -eq "coldstart") {
        Write-Host "`n[Cleanup] Restoring original KEDA cooldownPeriod..." -ForegroundColor Cyan
        Write-Host "Restoring original cooldownPeriod ($originalCooldown seconds)..."
        kubectl patch scaledobject $scaledObjectName -n football --type='merge' -p "{""spec"":{""cooldownPeriod"":$originalCooldown}}"
    }

    # Clean up environment variable
    Write-Host "`n[Cleanup] Cleaning up environment variables..." -ForegroundColor Cyan
    Remove-Item env:\K6_PROMETHEUS_RW_SERVER_URL
    Remove-Item env:\K6_FEATURES
    Write-Host "`nBatch execution finished." -ForegroundColor Cyan
}