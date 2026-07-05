param (
    [Parameter(Mandatory = $true)]
    [ValidateSet("wasm-rust", "wasm-js", "oci-axum", "oci-node", "oci-spring")]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidateSet("baseline", "coldstart", "scaling")]
    [string]$Scenario,

    [int]$Replays = 5,

    # The name of the K8s deployment to monitor during cold starts
    [string]$DeploymentName = "football-app",
    [string]$Namespace = "football",

    [string]$VictoriaMetricsUrl = "http://hetzner-vm:8428"
)

# --- Extract metadata from the Target parameter ---
$Parts = $Target -Split '-'
$Runtime = $Parts[0]   # "wasm" or "oci"
$Framework = $Parts[1]   # "rust", "js", "axum", "node", or "spring"

# --- Global Configurations ---
$PrometheusWriteUrl = "$VictoriaMetricsUrl/api/v1/write"
$env:K6_PROMETHEUS_RW_SERVER_URL = $PrometheusWriteUrl
$ScriptName = "load-test.js"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " BENCHMARK PROFILE" -ForegroundColor Cyan
Write-Host " Target:   $Runtime ($Framework)"
Write-Host " Scenario: $Scenario"
Write-Host " Replays:  $Replays loop(s)"
Write-Host "=========================================================" -ForegroundColor Cyan

if ($Scenario -ne "coldstart") {
    Write-Host "`n[Warmup] Executing a warmup run to stabilize the target..." -ForegroundColor Yellow
    k6 run --env scenario="warmup" $ScriptName
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
                break
            }
            Write-Host "." -NoNewline -ForegroundColor Gray
            Start-Sleep -Seconds 5
        }
        
        # 2. Energy Cooldown Window
        Write-Host "[Coldstart Setup] Holding for a 45-second energy cooling window..." -ForegroundColor Yellow
        Start-Sleep -Seconds 45
    }

    # --- BENCHMARK EXECUTION ---
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Replay $i of $Replays ..." -ForegroundColor Green

    # 1. CAPTURE EXACT START TIME (RFC3339 format required by VictoriaMetrics)
    $StartTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    # 2. RUN BENCHMARK
    k6 run `
        -o experimental-prometheus-rw `
        --env scenario=$Scenario `
        $ScriptName

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

# Clean up environment variable
Remove-Item env:\K6_PROMETHEUS_RW_SERVER_URL
Write-Host "`nBatch execution finished." -ForegroundColor Cyan