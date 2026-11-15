# Deploying to AWS (ECS Express Mode + RDS)

This is the runbook behind the "Deploying on AWS" section of the README. It reproduces the deployment that is running: **RDS PostgreSQL (pgvector) + two containers (API, UI) on ECS Express Mode**, each with a public HTTPS URL.

> Status: every command below was run against a real account (region `us-east-2`, Windows PowerShell 5.1, AWS CLI 2.40). Console labels may change; the CLI calls are the reliable part. Replace `<ACCOUNT_ID>`, `<rds-endpoint>` and the other placeholders with your own values, and never paste secrets into chat, commits or logs.

```
Browser -> UI service (Streamlit) --HTTPS + X-API-Key--> API service (FastAPI) --> RDS Postgres + pgvector
                                                                  \--> OpenAI
Secrets Manager: OPENAI_API_KEY, API_KEY, PG_READER_DSN, PG_LOGGER_DSN      CloudWatch Logs: both services
```

## 0. Account and guard rails
1. Sign up, enable MFA on the root user, and **create a budget alert** (Billing > Budgets) before creating anything.
2. Sign in the CLI: `aws login --region us-east-2 --profile olist-aws`, then `aws sts get-caller-identity --profile olist-aws`.
3. Use one region for everything. Some account types are tied to a single region.

## 1. RDS PostgreSQL
Create an instance: PostgreSQL, `db.t4g.micro`, 20 GB gp3, single AZ, default VPC, master user of your choice with **"Managed in AWS Secrets Manager"** credentials, storage autoscaling off, enhanced monitoring off, backup retention 1 day.
- While loading data, set **Public access: Yes** and allow port 5432 **only from your IP** in the instance's security group.
- Check with read-only calls: `aws rds describe-db-instances --db-instance-identifier <id> --query "DBInstances[0].{status:DBInstanceStatus,public:PubliclyAccessible,endpoint:Endpoint.Address,secret:MasterUserSecret.SecretArn}"`.

## 2. Load the data
```powershell
$env:PG_READER_PASSWORD = "<28+ letters and digits>"   # save both in a password manager
$env:PG_LOGGER_PASSWORD = "<28+ letters and digits>"
.\scripts\aws\load_rds.ps1 -Endpoint <rds-endpoint> -SecretArn <master-secret-arn> [-WithSkills]
```
The script reads the master password from Secrets Manager into memory, creates the `olist` database, checks that pgvector is available, then runs `migrate_to_postgres`, `setup_logging_db` and `ingest` (and `ingest_skills` with `-WithSkills`). Expect: all eleven tables loaded, 35 schema chunks, roles `olist_reader` (SELECT only) and `olist_logger` (INSERT only).

## 3. Runtime secrets
```powershell
.\scripts\aws\create_app_secret.ps1 -Endpoint <rds-endpoint>     # creates/updates secret "olist/app", prints only its ARN
```

## 4. Images
```powershell
.\scripts\aws\push_images.ps1 -Region us-east-2 -Profile olist-aws -Tag v1
```
Builds `Dockerfile` (API) and `Dockerfile.ui` (UI) from the repo root and pushes them to ECR. Notes: it logs in with a temporary Docker config (the `aws login` ECR token is too large for the Windows Credential Manager) and checks every exit code. Use a new tag for each release and keep the last good one.

## 5. IAM roles, cluster, logs
```powershell
$acct = "<ACCOUNT_ID>"; $p = "--profile","olist-aws"
# policy file with your account id filled in
(Get-Content deploy\iam\read-app-secret.template.json -Raw).Replace("<ACCOUNT_ID>", $acct) | Set-Content $env:TEMP\read-app-secret.json -Encoding ASCII

aws iam create-role --role-name olist-ecs-execution-role --assume-role-policy-document file://deploy/iam/trust-ecs-tasks.json @p
aws iam attach-role-policy --role-name olist-ecs-execution-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy @p
aws iam put-role-policy --role-name olist-ecs-execution-role --policy-name read-olist-app-secret --policy-document file://$env:TEMP\read-app-secret.json @p

aws iam create-role --role-name olist-ecs-infrastructure-role --assume-role-policy-document file://deploy/iam/trust-ecs.json @p
aws iam attach-role-policy --role-name olist-ecs-infrastructure-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices @p

aws ecs create-cluster --cluster-name olist @p --region us-east-2
foreach ($g in "/ecs/olist-api","/ecs/olist-ui") { aws logs create-log-group --log-group-name $g @p --region us-east-2; aws logs put-retention-policy --log-group-name $g --retention-in-days 14 @p --region us-east-2 }
```
The execution role can read **only** the `olist/app` secret. If the first service call fails with "Unable to assume the service linked role", wait a minute and retry (the roles need a moment to propagate).

## 6. Services
Copy `deploy/ecs/*.template.json`, replace `<ACCOUNT_ID>`, `<SUFFIX>` (the random suffix of the `olist/app` secret ARN), and the URL placeholders, then create the **API first**:
```powershell
aws ecs create-express-gateway-service --cli-input-json file://api-service.json --profile olist-aws --region us-east-2
```
- Each call returns the service's HTTPS URL (`ingressPaths[0].endpoint`). The service takes about 3 to 4 minutes to start; the URL answers 503 until the load balancer health check passes.
- Put the API's URL in the UI template's `API_URL`, and the UI's URL in the API template's `UI_BASE_URL`, then create the UI service. (If you create the API before you know the UI URL, update it afterwards with the update command below.)
- **Allow the API to reach the database:** find the API service's security group (`aws ec2 describe-security-groups --filters Name=vpc-id,Values=<vpc-id>`, the one whose description names the service) and add an inbound rule on the **database's** security group: TCP 5432 from that group.

## 7. Verify
- `Invoke-RestMethod <api-url>/health` returns `{"status":"ok"}` (it also queries the database).
- Without the key, any other endpoint returns 401. With the key (read it from Secrets Manager into a variable; do not print it): `POST /ask`, then `POST /execute`.
- Open the UI URL and ask a question. Check `aws logs tail /ecs/olist-api --since 10m`.
- Evals against the deployed API: `EVAL_API_URL=<api-url>` and `EVAL_API_KEY=<key>` with `python evals/scorer.py` (about 10 to 13 of 17).

## 8. Releasing and rolling back
```powershell
.\scripts\aws\push_images.ps1 -Region us-east-2 -Profile olist-aws -Tag v2
# update-express-gateway-service replaces the whole container definition, so send the full one:
aws ecs update-express-gateway-service --cli-input-json file://api-update.json --profile olist-aws --region us-east-2
```
`api-update.json` has `serviceArn`, `executionRoleArn`, `healthCheckPath` and the complete `primaryContainer` (image, port, logs, environment, secrets) with the new tag. Roll back by sending the previous tag. A rollout takes about 7 minutes. Watch it with `aws ecs describe-services --cluster olist --services olist-api --query "services[0].deployments"`.

## 9. After the demo
Make the database private (turn off public access; the services reach it through their security group), restrict or remove the public URLs, and **stop paying**: delete both services (`aws ecs delete-express-gateway-service --service-arn <arn>`), stop or delete the RDS instance, delete unused ECR images, and delete any CLI access keys. Check the Billing page the next day.
