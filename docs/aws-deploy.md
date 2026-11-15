# Deploying to AWS (first pass: ECS Express Mode + RDS)

Target for a first deploy: **RDS Postgres (pgvector) + two containers (API, UI) on ECS Express Mode**, each with a public HTTPS URL. This replaces Koyeb (API), Neon (database) and Streamlit Cloud (UI). Rebuilding the same thing as plain Fargate + ALB (and writing down what Express Mode hides) is the next step, see ROADMAP.md.

> Status: written without access to an AWS account, so console labels and CLI flags are **unverified**. Check each step against the current AWS docs and fix this file as you go.

```
Browser -> UI service (Streamlit, ECS) --HTTPS + X-API-Key--> API service (FastAPI, ECS) --> RDS Postgres + pgvector
                                                                       \--> OpenAI
Secrets Manager: OPENAI_API_KEY, API_KEY, PG_READER_DSN, PG_LOGGER_DSN      CloudWatch: logs from both services
```

## Order of work (about 2 hours if the account sign-up goes through quickly)

| Min | Step |
|---|---|
| 0-25 | Account, root MFA, **budget alert**, admin IAM user |
| 25-40 | AWS CLI + credentials; pick one region and stay in it |
| 40-65 | RDS instance (starts in ~10 min) and security group |
| 65-85 | Load data into RDS from your laptop; enable pgvector |
| 85-100 | ECR repos, build and push both images |
| 100-130 | API service (Express Mode), then UI service |
| 130-150 | Verify, run the live eval against the new URL, write down the cost |

## 0. Account and guard rails (do these first)
1. Sign up at aws.amazon.com (card + phone verification; activation can take a while).
2. **Root user: enable MFA.** Never use root for daily work.
3. **Billing > Budgets**: create a monthly cost budget of USD 20 with email alerts at 50% and 100%. (Also turn on "Billing alerts"/Cost Explorer.)
4. IAM: create an admin user (or IAM Identity Center user) for yourself. For the CLI create an access key, and **delete the key when you are done**. Do not commit it or paste it anywhere.

## 1. CLI
```powershell
winget install -e --id Amazon.AWSCLI
aws configure          # key id, secret, region (e.g. us-east-1), output json
aws sts get-caller-identity
```

## 2. RDS Postgres
Console > RDS > Create database: **PostgreSQL 16**, template *Free tier* or *Dev/Test*, `db.t4g.micro`, 20 GB gp3, single AZ, master user `olist_admin` with a strong password, DB name `olist`.
- Connectivity: default VPC; **Public access: Yes** for now (so your laptop can load the data); create a security group allowing **5432 only from your IP** (`https://checkip.amazonaws.com`).
- After loading data, switch **Public access to No** and allow 5432 only from the ECS services' security group.
- Cost: roughly USD 13-15/month while running; you can **Stop temporarily** (auto-restarts after 7 days).

## 3. Load data (from your laptop, in `AI Agent/`)
```powershell
$env:PG_ADMIN_DSN = "postgresql://olist_admin:<admin-pw>@<rds-endpoint>:5432/olist?sslmode=require"
$env:PG_READER_PASSWORD = "<long mixed reader password>"
$env:PG_LOGGER_PASSWORD = "<long mixed logger password>"
$env:OPENAI_API_KEY = "<key>"
python -m migrate_to_postgres
python -m setup_logging_db --import-jsonl
python -m ingest
# only if deploying the skills branch:  python -m ingest_skills
```
(`CREATE EXTENSION vector` is run by `ingest`; the RDS master user is allowed to.)

## 4. Secrets
Secrets Manager > Store a new secret (one JSON secret named `olist/app`):
`OPENAI_API_KEY`, `API_KEY`, `PG_READER_DSN`, `PG_LOGGER_DSN` (DSNs use the **RDS endpoint**, roles `olist_reader` / `olist_logger`, `?sslmode=require`).
Give the ECS task execution role `secretsmanager:GetSecretValue` on that secret only (least privilege).

## 5. Images
```powershell
.\scripts\aws\push_images.ps1 -Region us-east-1        # builds both images, creates ECR repos, pushes
```

## 6. Services (ECS Express Mode)
Console > ECS > Express Mode > Create service. For the **API**: image `olist-api`, container port **8080**, health check path **`/health`**, CPU 0.5 vCPU / 1 GB, env/secrets from `olist/app` (plus `DB_POOL_MAX=3`, `ENV=prod`). Note the generated HTTPS URL.
For the **UI**: image `olist-ui`, port **8080**, health check path **`/_stcore/health`**, env `API_URL=<API url>` and `API_KEY` from the secret.
Inspect what Express Mode created (cluster, service, task definition, ALB, security groups, roles): that list is the material for the "what Express Mode hides" ADR.

## 7. Verify
- `Invoke-RestMethod <api-url>/health` returns ok; ask one question through the UI.
- Live eval: `$env:EVAL_API_URL="<api-url>"; $env:EVAL_API_KEY=$env:API_KEY; python evals/scorer.py evals/eval_results_aws_run1.json` (expect about 10-13 of 17).
- CloudWatch Logs: both services log; the API prints one JSON line per request.
- Restrict the UI (it spends your OpenAI credits): anyone with the URL can use it. Options: keep the URL private, put the ALB behind an IP allowlist, or add auth later.

## 8. Cleanup / cost
Stop RDS when idle; delete both services and the ECR images when you are finished with the demo; delete the IAM access key. Check Billing the next day.
