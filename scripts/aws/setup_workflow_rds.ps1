# Add the dispute-workflow database objects to the EXISTING RDS database and wire the new role's
# connection string into the app secret. Purely additive: it never drops or rewrites the Olist
# tables (unlike load_rds.ps1, whose migrate_to_postgres step recreates them - do NOT re-run that).
#
# Usage (repo root, PowerShell):
#   .\scripts\aws\setup_workflow_rds.ps1 -Endpoint <rds-endpoint> -SecretArn <master secret arn> -CheckOnly   # read-only look
#   .\scripts\aws\setup_workflow_rds.ps1 -Endpoint <rds-endpoint> -SecretArn <master secret arn>             # do it
#
# What it does (without -CheckOnly)
#   1. reads the RDS master credentials from Secrets Manager (memory only)
#   2. generates a random 32-character password for the olist_workflow role (never printed or saved to disk)
#   3. runs `python -m setup_workflow_db` against RDS (CREATE ... IF NOT EXISTS, role + GRANTs)
#   4. adds ONE key, PG_WORKFLOW_DSN, to the app secret and leaves every other key untouched
#      (re-running create_app_secret.ps1 instead would overwrite the whole secret and may rotate API_KEY)
#   5. connects as the new role and checks the permissions that matter
# Re-running is safe: the schema is IF NOT EXISTS and a new password simply replaces the old one.
param(
    [Parameter(Mandatory = $true)][string]$Endpoint,
    [Parameter(Mandatory = $true)][string]$SecretArn,
    [string]$AppSecretName = "olist/app",
    [string]$Region = "us-east-2",
    [string]$Profile = "olist-aws",
    [string]$DatabaseName = "olist",
    [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe" }
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# 1. Master credentials (memory only).
$m = & $aws secretsmanager get-secret-value --secret-id $SecretArn --profile $Profile --region $Region `
    --query SecretString --output text | ConvertFrom-Json
if ($LASTEXITCODE) { throw "Could not read the master secret." }
$adminDsn = "postgresql://$($m.username):$([uri]::EscapeDataString($m.password))@${Endpoint}:5432/${DatabaseName}?sslmode=require"

# Current app secret: key names (and values, in memory) so we can prove nothing else changes.
$app = & $aws secretsmanager get-secret-value --secret-id $AppSecretName --profile $Profile --region $Region `
    --query SecretString --output text | ConvertFrom-Json
if ($LASTEXITCODE) { throw "Could not read the app secret." }
$before = @{}; $app.PSObject.Properties | ForEach-Object { $before[$_.Name] = $_.Value }
Write-Host "App secret keys now: $(($before.Keys | Sort-Object) -join ', ')"

# Helper: run a Python snippet from a file (Windows PowerShell 5.1 mangles quotes in -c arguments).
function Invoke-Py([string]$code, [hashtable]$envVars) {
    $f = Join-Path $env:TEMP "olist_wf_check.py"
    Set-Content -Path $f -Value $code -Encoding UTF8
    foreach ($k in $envVars.Keys) { Set-Item "Env:$k" $envVars[$k] }
    try {
        & $py $f | Out-Host          # show the output but do not let it become the function's return value
        $code = $LASTEXITCODE
        return [int]$code
    } finally {
        Remove-Item $f -ErrorAction SilentlyContinue
        foreach ($k in $envVars.Keys) { Remove-Item "Env:$k" -ErrorAction SilentlyContinue }
    }
}

# 2. Read-only look at the database.
$look = @'
import os, sys, psycopg
try:
    c = psycopg.connect(os.environ["DSN"], connect_timeout=15)
except Exception as e:
    print("CONNECT FAILED:", str(e).strip().splitlines()[0]); sys.exit(2)
print("server:", c.execute("show server_version").fetchone()[0])
n = c.execute("select count(*) from information_schema.tables where table_schema='public'").fetchone()[0]
print("public tables (Olist data):", n)
print("orders rows:", c.execute("select count(*) from orders").fetchone()[0])
print("workflow schema exists:", bool(c.execute("select 1 from information_schema.schemata where schema_name='workflow'").fetchone()))
print("role olist_workflow exists:", bool(c.execute("select 1 from pg_roles where rolname='olist_workflow'").fetchone()))
'@
$rc = Invoke-Py $look @{ DSN = $adminDsn }
if ($rc -ne 0) { throw "Database check failed (exit $rc). If it timed out, your IP may not be allowed on the RDS security group." }
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing was changed."; return }

# 3. Generate the role password and create the schema + role.
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$alphabet = (48..57) + (65..90) + (97..122)
$password = -join ($bytes | ForEach-Object { [char]$alphabet[$_ % $alphabet.Count] })

$env:PG_ADMIN_DSN = $adminDsn; $env:PG_DB = $DatabaseName; $env:PG_WORKFLOW_PASSWORD = $password
Push-Location (Join-Path $repo "AI Agent")
try {
    & $py -m setup_workflow_db
    if ($LASTEXITCODE) { throw "setup_workflow_db failed" }
} finally {
    Pop-Location
    Remove-Item Env:PG_ADMIN_DSN, Env:PG_WORKFLOW_PASSWORD -ErrorAction SilentlyContinue
}

# 4. Add PG_WORKFLOW_DSN to the app secret; every other key keeps its value.
$wfDsn = "postgresql://olist_workflow:$([uri]::EscapeDataString($password))@${Endpoint}:5432/${DatabaseName}?sslmode=require"
$app | Add-Member -NotePropertyName PG_WORKFLOW_DSN -NotePropertyValue $wfDsn -Force
$tmp = Join-Path $env:TEMP "olist_app_secret.json"
try {
    $app | ConvertTo-Json | Set-Content -Path $tmp -Encoding ASCII
    & $aws secretsmanager put-secret-value --secret-id $AppSecretName --secret-string "file://$tmp" `
        --profile $Profile --region $Region --query VersionId --output text | Out-Null
    if ($LASTEXITCODE) { throw "put-secret-value failed. The role password was already changed; re-run this script." }
} finally { Remove-Item $tmp -ErrorAction SilentlyContinue }

$after = & $aws secretsmanager get-secret-value --secret-id $AppSecretName --profile $Profile --region $Region `
    --query SecretString --output text | ConvertFrom-Json
$bad = @($before.Keys | Where-Object { $after.$_ -ne $before[$_] })
if ($bad.Count) { throw "Secret verification FAILED: these keys changed: $($bad -join ', ')" }
if (-not $after.PG_WORKFLOW_DSN) { throw "Secret verification FAILED: PG_WORKFLOW_DSN is missing." }
Write-Host "App secret keys now: $(($after.PSObject.Properties.Name | Sort-Object) -join ', ') (existing values unchanged)"

# 5. Check the new role from the secret's own DSN.
$verify = @'
import os, sys, psycopg
c = psycopg.connect(os.environ["DSN"], autocommit=True, connect_timeout=15)
ok = True
def expect_denied(label, sql):
    global ok
    try:
        c.execute(sql); print("  FAIL (allowed):", label); ok = False
    except psycopg.errors.InsufficientPrivilege:
        print("  ok, refused:", label)
print("connected as:", c.execute("select current_user").fetchone()[0])
print("  ok, can read:", c.execute("select count(*) from workflow.disputes").fetchone()[0], "disputes")
expect_denied("UPDATE on audit_log", "update workflow.audit_log set reasoning = 'x'")
expect_denied("DELETE on audit_log", "delete from workflow.audit_log")
expect_denied("reading the Olist orders table", "select 1 from public.orders limit 1")
sys.exit(0 if ok else 3)
'@
Write-Host "`nChecking the new role:"
$rc = Invoke-Py $verify @{ DSN = $after.PG_WORKFLOW_DSN }
if ($rc -ne 0) { throw "Role verification failed (exit $rc)." }
Write-Host "`nDone. The workflow schema and role exist, and PG_WORKFLOW_DSN is in '$AppSecretName'."
Write-Host "Running services do not use it yet: add it to the API task definition and redeploy."
