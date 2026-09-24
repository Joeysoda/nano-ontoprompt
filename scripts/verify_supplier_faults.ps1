param(
  [int]$RepeatRuns = 50,
  [int]$PollSeconds = 1,
  [int]$PollTimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$composeBase = @('-f', 'docker-compose.yml', '-f', 'docker-compose.local.yml')
$compose = $composeBase
$api = 'http://127.0.0.1:8000/api/v2'

function Compose([string[]]$ComposeArgs) {
  & docker compose @compose @ComposeArgs
  if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $($ComposeArgs -join ' ')" }
}

function Api([string]$Method, [string]$Path, $Body = $null) {
  $params = @{ Method = $Method; Uri = "$api$Path"; ContentType = 'application/json' }
  if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 20 -Compress) }
  Invoke-RestMethod @params
}

function Wait-Run([string]$OntologyId, [string]$RunId, [string[]]$Terminal = @('completed', 'failed', 'cancelled')) {
  $deadline = (Get-Date).AddSeconds($PollTimeoutSeconds)
  do {
    $run = Api 'GET' "/ontologies/$OntologyId/scenario-runs/$RunId"
    if ($Terminal -contains $run.status) { return $run }
    Start-Sleep -Seconds $PollSeconds
  } while ((Get-Date) -lt $deadline)
  throw "Timed out waiting for run $RunId; last status=$($run.status), stage=$($run.current_stage)"
}

function Wait-Status([string]$OntologyId, [string]$RunId, [string[]]$Statuses) {
  $deadline = (Get-Date).AddSeconds(30)
  do {
    $run = Api 'GET' "/ontologies/$OntologyId/scenario-runs/$RunId"
    if ($Statuses -contains $run.status) { return $run }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  throw "Timed out waiting for run $RunId to reach $($Statuses -join ',')"
}

function Wait-Api {
  $deadline = (Get-Date).AddSeconds(60)
  do {
    try {
      $response = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 3
      if ($response.StatusCode -eq 200) { return }
    } catch { Start-Sleep -Seconds 1 }
  } while ((Get-Date) -lt $deadline)
  throw 'Backend did not become ready within 60 seconds'
}

Write-Host 'Checking live Compose dependencies...'
Compose @('up', '-d', 'backend', 'celery_worker', 'falkordb')
Wait-Api
$catalog = Api 'POST' '/what-if/demo/bootstrap'
$study = Api 'GET' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)"
$baseline = $study.cases | Where-Object key -eq 'baseline' | Select-Object -First 1
$candidate = $study.cases | Where-Object key -eq 'supplier_b' | Select-Object -First 1
if (-not $baseline -or -not $candidate) { throw 'Prepared Baseline/Supplier B cases were not returned' }

Write-Host '1/3 Worker interruption and recovery'
$compose = $composeBase + @('-f', 'docker-compose.fault-test.yml')
try {
  Compose @('up', '-d', '--no-deps', '--force-recreate', 'celery_worker')
  $workerRun = Api 'POST' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)/cases/$($baseline.id)/runs" @{
    etag = $baseline.etag; client_request_id = "fault-worker-$([guid]::NewGuid())"
  }
  Wait-Status $catalog.ontology_id $workerRun.id @('running') | Out-Null
  Compose @('kill', '-s', 'SIGKILL', 'celery_worker')
} finally {
  $compose = $composeBase
  Compose @('up', '-d', '--no-deps', '--force-recreate', 'celery_worker')
}
$interrupted = Wait-Run $catalog.ontology_id $workerRun.id
if ($interrupted.status -ne 'failed' -or $interrupted.error.code -ne 'worker_interrupted') {
  throw "Worker recovery did not produce worker_interrupted: run=$($interrupted.id), status=$($interrupted.status), error=$($interrupted.error.code)"
}
$workerRetry = Api 'POST' ("/ontologies/{0}/scenario-runs/{1}:retry" -f $catalog.ontology_id, $workerRun.id) @{ client_request_id = "fault-worker-retry-$([guid]::NewGuid())" }
$workerRetryResult = Wait-Run $catalog.ontology_id $workerRetry.id
if ($workerRetryResult.status -ne 'completed') { throw 'Worker retry did not complete' }

Write-Host '2/3 FalkorDB outage and retry'
Compose @('stop', 'falkordb')
try {
  $falkorRun = Api 'POST' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)/cases/$($candidate.id)/runs" @{
    etag = $candidate.etag; client_request_id = "fault-falkor-$([guid]::NewGuid())"
  }
  $falkorFailed = Wait-Run $catalog.ontology_id $falkorRun.id
} finally {
  Compose @('up', '-d', 'falkordb')
}
if ($falkorFailed.status -ne 'failed' -or $falkorFailed.error.code -notin @('stage_failed', 'baseline_dependency_failed')) {
  throw "FalkorDB outage did not fail explicitly: run=$($falkorFailed.id), status=$($falkorFailed.status), error=$($falkorFailed.error.code)"
}
Start-Sleep -Seconds 3
$falkorRetry = Api 'POST' ("/ontologies/{0}/scenario-runs/{1}:retry" -f $catalog.ontology_id, $falkorRun.id) @{ client_request_id = "fault-falkor-retry-$([guid]::NewGuid())" }
$falkorRetryResult = Wait-Run $catalog.ontology_id $falkorRetry.id
if ($falkorRetryResult.status -ne 'completed') { throw 'FalkorDB retry did not complete' }

Write-Host "3/3 $RepeatRuns repeated real Runs and idempotency"
$requestId = "repeat-idempotent-$([guid]::NewGuid())"
$first = Api 'POST' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)/cases/$($baseline.id)/runs" @{ etag = $baseline.etag; client_request_id = $requestId }
$second = Api 'POST' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)/cases/$($baseline.id)/runs" @{ etag = $baseline.etag; client_request_id = $requestId }
if ($first.id -ne $second.id) { throw 'Identical client_request_id created more than one Run' }
$firstResult = Wait-Run $catalog.ontology_id $first.id
if ($firstResult.status -ne 'completed') { throw 'Idempotent baseline run did not complete' }

$runs = @()
for ($i = 1; $i -le $RepeatRuns; $i++) {
  $run = Api 'POST' "/ontologies/$($catalog.ontology_id)/scenario-studies/$($catalog.study_id)/cases/$($baseline.id)/runs" @{
    etag = $baseline.etag; client_request_id = "repeat-$i-$([guid]::NewGuid())"
  }
  $result = Wait-Run $catalog.ontology_id $run.id
  if ($result.status -ne 'completed') { throw "Repeated Run $i failed with $($result.status)" }
  $runs += $result
  Write-Host "  $i/$RepeatRuns completed ($($result.duration_ms) ms)"
}
$ids = @($runs | Select-Object -ExpandProperty id -Unique)
$digests = @($runs | Select-Object -ExpandProperty output_digest -Unique)
if ($ids.Count -ne $RepeatRuns) { throw "Expected $RepeatRuns unique Run IDs, got $($ids.Count)" }
if ($digests.Count -ne 1) { throw "Repeated Runs produced non-deterministic output digests: $($digests -join ', ')" }

Write-Host "PASS: worker recovery, FalkorDB retry, $RepeatRuns real Runs, and idempotency verified."
