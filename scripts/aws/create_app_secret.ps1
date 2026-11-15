# Store the app's runtime secrets in ONE Secrets Manager secret (JSON), for ECS to inject as env vars.
#
# Usage (same PowerShell window that holds the two app passwords; from the repo root):
#   .\scripts\aws\create_app_secret.ps1 -Endpoint <rds-endpoint>
#
# Reads: PG_READER_PASSWORD, PG_LOGGER_PASSWORD (env), OPENAI_API_KEY (env or "AI Agent\.env"),
#        API_KEY (env; generated and kept in $env:API_KEY if not set).
# Prints only the secret ARN. Values are never echoed; the temp file is deleted straight after use.
param(
    [Parameter(Mandatory = $true)][string]$Endpoint,
    [string]$SecretName = "olist/app",
    [string]$Region = "us-east-2",
    [string]$Profile = "olist-aws",
    [string]$DatabaseName = "olist"
)
$ErrorActionPreference = "Stop"

foreach ($v in "PG_READER_PASSWORD", "PG_LOGGER_PASSWORD") {
    if (-not (Get-Item "Env:$v" -ErrorAction SilentlyContinue)) { throw "Set $v first (same value used for load_rds.ps1)." }
}
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe" }

# OpenAI key: environment first, then AI Agent\.env (KEY=value lines).
$openai = $env:OPENAI_API_KEY
if (-not $openai) {
    $envFile = Join-Path $PSScriptRoot "..\..\AI Agent\.env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*OPENAI_API_KEY\s*=' } | Select-Object -First 1
        if ($line) { $openai = ($line -replace '^\s*OPENAI_API_KEY\s*=\s*', '').Trim().Trim('"').Trim("'") }
    }
}
if (-not $openai) { throw "OPENAI_API_KEY not found in the environment or AI Agent\.env." }

if (-not $env:API_KEY) {
    $env:API_KEY = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    Write-Host "Generated a new API_KEY (kept in `$env:API_KEY in this window). Save it in your password manager."
}

$reader = [uri]::EscapeDataString($env:PG_READER_PASSWORD)
$logger = [uri]::EscapeDataString($env:PG_LOGGER_PASSWORD)
$payload = [ordered]@{
    OPENAI_API_KEY = $openai
    API_KEY        = $env:API_KEY
    PG_READER_DSN  = "postgresql://olist_reader:${reader}@${Endpoint}:5432/${DatabaseName}?sslmode=require"
    PG_LOGGER_DSN  = "postgresql://olist_logger:${logger}@${Endpoint}:5432/${DatabaseName}?sslmode=require"
}

$tmp = Join-Path $env:TEMP "olist_app_secret.json"
try {
    $payload | ConvertTo-Json | Set-Content -Path $tmp -Encoding ASCII
    # "Does it exist?" is expected to fail the first time. Windows PowerShell 5.1 turns a native
    # program's stderr into a terminating error under 'Stop', so relax it just for this probe.
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $aws secretsmanager describe-secret --secret-id $SecretName --profile $Profile --region $Region 2>&1 | Out-Null
    $exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    if ($exists) {
        $arn = & $aws secretsmanager put-secret-value --secret-id $SecretName --secret-string "file://$tmp" `
            --profile $Profile --region $Region --query ARN --output text
        Write-Host "Updated existing secret."
    } else {
        $arn = & $aws secretsmanager create-secret --name $SecretName --description "Olist analytics agent runtime config" `
            --secret-string "file://$tmp" --profile $Profile --region $Region --query ARN --output text
        Write-Host "Created secret."
    }
} finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0) { throw "Secrets Manager call failed." }
Write-Host "Secret ARN: $arn"
Write-Host "Keys stored: OPENAI_API_KEY, API_KEY, PG_READER_DSN, PG_LOGGER_DSN"
