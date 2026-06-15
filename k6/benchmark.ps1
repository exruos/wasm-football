param (
    [Parameter(Mandatory = $true)]
    [ValidateSet("wasm-rust", "wasm-js", "container-axum", "container-node", "container-spring")]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidateSet("baseline", "coldstart", "scaling")]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [ValidateSet("simple", "lookup", "aggregate")]
    [string]$Endpoint,

    [int]$Replays = 5,

    # The name of your K8s deployment to scale down during cold starts
    [string]$DeploymentName = "football-rust", # TODO look up
    [string]$Namespace = "football"
)

# --- Extract metadata from the Target parameter ---
$Parts = $Target -Split '-'
$Runtime = $Parts[0]   # "wasm" or "container"
$Framework = $Parts[1]   # "rust", "js", "axum", "node", or "spring"

# --- Global Configurations ---
$PrometheusUrl = "http://hetzner-vm:8428/api/v1/write"
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

    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Replay $i of $Replays (ID: $RunId)..." -ForegroundColor Green

    # Execute k6
    k6 run `
        -o experimental-prometheus-rw `
        -o json=$JsonFilename `
        --env thesis_scenario=$Scenario `
        --tag thesis_runtime=$Runtime `
        --tag thesis_framework=$Framework `
        --tag thesis_scenario=$Scenario `
        --tag thesis_endpoint=$Endpoint `
        --tag thesis_iteration=$i `
        --tag run_id=$RunId `
        $ScriptName

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