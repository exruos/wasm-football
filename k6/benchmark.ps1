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

    # The name of your K8s deployment to scale down during cold starts
    [string]$DeploymentName = "football-rust", # TODO look up
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
Write-Host " THESIS BENCHMARK PROFILE ACTIVATED" -ForegroundColor Cyan
Write-Host " Target:   $Runtime ($Framework)"
Write-Host " Scenario: $Scenario"
Write-Host " Endpoint: $Endpoint"
Write-Host " Replays:  $Replays loop(s)"
Write-Host "=========================================================" -ForegroundColor Cyan

# --- Safe execution loop ---
for ($i = 1; $i -le $Replays; $i++) {

    # --- COLDSTART PREPARATION WORKFLOW ---
    if ($Scenario -eq "coldstart") {
        Write-Host "`n[Coldstart Setup] Scaling deployment '$DeploymentName' down to 0..." -ForegroundColor Magenta
        
        # 1. Force the scale down
        kubectl scale deployment $DeploymentName --replicas=0 -n $Namespace | Out-Null
        
        # 2. Dynamically wait until all pods are completely terminated
        Write-Host "[Coldstart Setup] Waiting for active pods to terminate..." -ForegroundColor Magenta
        while ($true) {
            $ActivePods = kubectl get pods -n $Namespace --no-headers 2>$null | Where-Object { $_ -match "Running|Terminating|ContainerCreating" }
            if ($null -eq $ActivePods) {
                Write-Host "[Coldstart Setup] All pods terminated successfully." -ForegroundColor Green
                break
            }
            Write-Host "." -NoNewline -ForegroundColor Gray
            Start-Sleep -Seconds 3
        }
        
        # 3. Energy Cooldown Window
        # This allows the physical node's CPU power and memory allocation metrics 
        # to drop completely back down to an idle baseline after pod deletion noise.
        Write-Host "[Coldstart Setup] Holding for a 45-second energy cooling window..." -ForegroundColor Yellow
        Start-Sleep -Seconds 45
    }

    # --- BENCHMARK EXECUTION ---
    # Append the endpoint type to the Run ID and JSON filename for perfect filtering later
    $Timestamp = (Get-Date -UFormat %s) -replace '\..*'
    $RunId = "$Runtime-$Framework-$Scenario-$Endpoint-r$i-$Timestamp"
    $JsonFilename = "metrics-$RunId.json"
    $VmJsonFilename = "vm-metrics-$RunId.json"

    
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Replay $i of $Replays (ID: $RunId)..." -ForegroundColor Green

    # 1. CAPTURE EXACT START TIME (RFC3339 format required by VictoriaMetrics)
    $StartTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    # 1.1 scale the deployment back up immediately before the run to trigger the startup sequence
    if ($Scenario -eq "coldstart") {
        kubectl scale deployment $DeploymentName --replicas=1 -n $Namespace | Out-Null
    }

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

    # We target metrics that carry our specific runtime label to keep the JSON clean
    $MatchFilters = @(
        "{scenario=`"$Scenario`",iteration=`"$i`"}", # Catch all k6 metrics
        "{namespace=`"$Namespace`"}",                # Catch all pod CPU/RAM metrics
        "{__name__=~`^kepler_.*`}"                   # Catch all Kepler energy metrics
    )

    $UrlParams = ""
    foreach ($Filter in $MatchFilters) {
        $UrlParams += "&match[]=" + [Uri]::EscapeDataString($Filter)
    }

    $ExportUrl = "$VictoriaMetricsUrl/api/v1/export?start=$StartTime&end=$EndTime$UrlParams"

    # Save the JSON stream
    Invoke-WebRequest -Uri $ExportUrl -OutFile $VmJsonFilename

    Write-Host "[Exporting Data] Saved database snapshot to $VmJsonFilename" -ForegroundColor Green

    # --- POST-RUN COOLDOWN ---
    if ($i -lt $Replays) {
        if ($Scenario -eq "coldstart") {
            Write-Host "Replay $i finished. Preparing for next cold-start cycle..." -ForegroundColor Yellow
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