param (
    [Parameter(Mandatory = $true)]
    [ValidateSet("wasm-rust", "wasm-js", "oci-axum", "oci-node", "oci-spring")]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidateSet("baseline", "coldstart", "scaling")]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [ValidateSet("simple", "detailed", "lookup", "aggregate")]
    [string]$Endpoint,

    [int]$Replays = 5,

    # The name of your K8s deployment to monitor during cold starts
    [string]$DeploymentName = "football-app",
    [string]$Namespace = "football",

    [string]$VictoriaMetricsUrl = "http://hetzner-vm:8428"
)

# --- Extract metadata from the Target parameter ---
$Parts = $Target -Split '-'
$Runtime = $Parts[0]   # "wasm" or "oci"
$Framework = $Parts[1]   # "rust", "js", "axum", "node", or "spring"

# --- Global Configurations ---
$PrometheusUrl = "$VictoriaMetricsUrl/api/v1/write"
$env:K6_PROMETHEUS_RW_SERVER_URL = $PrometheusUrl
$ScriptName = "load-test.js"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " BENCHMARK PROFILE" -ForegroundColor Cyan
Write-Host " Target:   $Runtime ($Framework)"
Write-Host " Scenario: $Scenario"
Write-Host " Endpoint: $Endpoint"
Write-Host " Replays:  $Replays loop(s)"
Write-Host "=========================================================" -ForegroundColor Cyan

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
    # Append the endpoint type to the Run ID and JSON filename for perfect filtering later
    $Timestamp = (Get-Date -UFormat %s) -replace '\..*'
    $RunId = "$Runtime-$Framework-$Scenario-$Endpoint-r$i-$Timestamp"
    $JsonFilename = "metrics-$RunId.json"
    $VmJsonFilename = "vm-metrics-$RunId.jsonl"
    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Replay $i of $Replays (ID: $RunId)..." -ForegroundColor Green

    # 1. CAPTURE EXACT START TIME (RFC3339 format required by VictoriaMetrics)
    $StartTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    # 2. RUN BENCHMARK
    k6 run `
        -o experimental-prometheus-rw `
        -o json=$JsonFilename `
        --env scenario=$Scenario `
        --env endpoint=$Endpoint `
        --tag runtime=$Runtime `
        --tag framework=$Framework `
        --tag scenario=$Scenario `
        --tag endpoint=$Endpoint `
        --tag iteration=$i `
        $ScriptName

    # 3. CAPTURE EXACT END TIME (Add a 5-second pad to catch late-flushing metrics)
    Start-Sleep -Seconds 5
    $EndTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    # 4. EXPORT RAW DATABASE METRICS FOR THIS SPECIFIC TIMEFRAME
    Write-Host "[Exporting Data] Fetching raw window from VictoriaMetrics..." -ForegroundColor Cyan

    # 4.1 Define isolated PromQL selectors 
    # Kepler 0.11 pod-level energy metrics in your namespace
    $MatchKepler = [uri]::EscapeDataString('{__name__=~"kepler_container_.*", namespace="football", pod!=""}')
    
    # Standard cAdvisor CPU/RAM pod metrics in your namespace
    $MatchPods = [uri]::EscapeDataString('{__name__=~"container_cpu_usage_seconds_total|container_memory_working_set_bytes", namespace="football", pod!=""}')
    
    # All k6 load testing metrics (no namespace filter since k6 runs externally)
    $MatchK6 = [uri]::EscapeDataString('{__name__=~"k6_.*"}')

    # 4.2 Construct the URL-encoded payload safely
    $Body = "start=$StartTime&end=$EndTime&match[]=$([uri]::EscapeDataString($MatchKepler))&match[]=$([uri]::EscapeDataString($MatchPods))&match[]=$([uri]::EscapeDataString($MatchK6))"

    # 4.3 Print out the target URL, query parameters, and the literal request body for debugging
    $ExportUrl = "$VictoriaMetricsUrl/api/v1/export"
    Write-Host "`n--- VictoriaMetrics Export Request ---" -ForegroundColor DarkGray
    Write-Host "Target URL: $ExportUrl" -ForegroundColor DarkGray
    Write-Host "Time Range: $StartTime -> $EndTime" -ForegroundColor DarkGray
    Write-Host "Match [1]:  $MatchKepler" -ForegroundColor DarkGray
    Write-Host "Match [2]:  $MatchPods" -ForegroundColor DarkGray
    Write-Host "Match [3]:  $MatchK6" -ForegroundColor DarkGray
    Write-Host "HTTP Body:  $Body" -ForegroundColor Yellow
    Write-Host "--------------------------------------`n" -ForegroundColor DarkGray

    # We target metrics that carry our specific runtime label to keep the JSON clean
#     $MatchQuery = @"
# {__name__=~"kepler_*|
# pod_cpu_.*|
# pod_memory_working_set_bytes|
# kube_pod_info{namespace="football"}|
# k6_.*"}
# "@ -replace "`r`n|`n|\s", ""

#     $Params = @{
#         "start"   = $StartTime
#         "end"     = $EndTime
#         "match[]" = $MatchQuery
#     }

    # Save the JSON stream
    $OutputDir = ".\.output"
    if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir }
    Invoke-RestMethod -Uri "$VictoriaMetricsUrl/api/v1/export" `
        -Method Post `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $Body `
        -OutFile $OutputDir\$VmJsonFilename

    Write-Host "[Exporting Data] Saved database snapshot to $OutputDir\$VmJsonFilename" -ForegroundColor Green

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
Write-Host "`nBatch execution finished. Data safely pushed and archived." -ForegroundColor Cyan