# Load the Olist data into RDS: create the database if needed, then run the three setup scripts.
#
# Usage (from the repo root, in a PowerShell window that already has the two app passwords set):
#   $env:PG_READER_PASSWORD = "<long letters+digits>"
#   $env:PG_LOGGER_PASSWORD = "<long letters+digits>"
#   .\scripts\aws\load_rds.ps1 -Endpoint <rds-endpoint> -SecretArn <master secret arn>
#
# The master password is read from Secrets Manager at run time. It is never printed or written to disk.
# The OpenAI key comes from "AI Agent\.env" (python-dotenv), exactly as for local runs.
param(
    [Parameter(Mandatory = $true)][string]$Endpoint,
    [Parameter(Mandatory = $true)][string]$SecretArn,
    [string]$Region = "us-east-2",
    [string]$Profile = "olist-aws",
    [string]$DatabaseName = "olist",
    [switch]$WithSkills   # also run ingest_skills (needs the skills-orchestrator code)
)
$ErrorActionPreference = "Stop"

if (-not $env:PG_READER_PASSWORD -or -not $env:PG_LOGGER_PASSWORD) {
    throw "Set PG_READER_PASSWORD and PG_LOGGER_PASSWORD first (see the usage comment)."
}
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe" }

# 1. Master credentials from Secrets Manager (kept in memory only).
$secret = & $aws secretsmanager get-secret-value --secret-id $SecretArn --profile $Profile --region $Region `
    --query SecretString --output text | ConvertFrom-Json
$user = $secret.username
$pw = [uri]::EscapeDataString($secret.password)   # generated passwords can contain URL-special characters
$base = "postgresql://${user}:${pw}@${Endpoint}:5432"

# 2. Connectivity + database + pgvector check, via the default 'postgres' database.
$env:BOOT_DSN = "$base/postgres?sslmode=require"
$env:TARGET_DB = $DatabaseName
$py = @'
import os, sys, psycopg
try:
    c = psycopg.connect(os.environ["BOOT_DSN"], autocommit=True, connect_timeout=15)
except Exception as e:
    print("CONNECT FAILED:", str(e).strip().splitlines()[0]); sys.exit(2)
db = os.environ["TARGET_DB"]
print("server:", c.execute("show server_version").fetchone()[0])
if not c.execute("select 1 from pg_database where datname=%s", (db,)).fetchone():
    c.execute(f'create database "{db}"'); print("created database", db)
else:
    print("database exists:", db)
v = c.execute("select default_version from pg_available_extensions where name='vector'").fetchone()
print("pgvector available:", v[0] if v else "NO")
sys.exit(0 if v else 3)
'@
# Run from a file: Windows PowerShell 5.1 strips double quotes from native-command arguments.
$check = Join-Path $env:TEMP "olist_rds_check.py"
Set-Content -Path $check -Value $py -Encoding UTF8
python $check
$rc = $LASTEXITCODE
Remove-Item $check -ErrorAction SilentlyContinue
Remove-Item Env:BOOT_DSN -ErrorAction SilentlyContinue
if ($rc -ne 0) { throw "Pre-check failed (exit $rc). See the message above." }

# 3. The same three scripts used for Neon, now pointed at RDS.
$env:PG_ADMIN_DSN = "$base/${DatabaseName}?sslmode=require"
$env:PG_DB = $DatabaseName
Push-Location (Join-Path $PSScriptRoot "..\..\AI Agent")
try {
    python -m migrate_to_postgres;               if ($LASTEXITCODE) { throw "migrate_to_postgres failed" }
    python -m setup_logging_db --import-jsonl;   if ($LASTEXITCODE) { throw "setup_logging_db failed" }
    python -m ingest;                            if ($LASTEXITCODE) { throw "ingest failed" }
    if ($WithSkills) { python -m ingest_skills;  if ($LASTEXITCODE) { throw "ingest_skills failed" } }
} finally {
    Pop-Location
    Remove-Item Env:PG_ADMIN_DSN -ErrorAction SilentlyContinue
}
Write-Host "`nDone. Data, roles, log tables and schema embeddings are in RDS ($Endpoint)."
