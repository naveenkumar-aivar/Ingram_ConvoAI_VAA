# Deployment — AgentCore + Lambda + API Gateway

The async job architecture and the AWS resources backing it.

## Flow

A SINGLE Lambda (`vaa-api`) plays both roles — front door and async worker —
by detecting the event shape and self-invoking for the worker role.

```
Browser
  │  POST {API}/jobs {action, payload}                → {job_id} (202, instant)
  │  GET  {API}/jobs/{job_id}  (poll)                 → {status, result|error}
  ▼
API Gateway (HTTP API)  ──routes──►  vaa-api (Lambda, API role)
                                       │ writes PENDING → vaa-jobs (DynamoDB)
                                       │ self-invokes vaa-api (InvocationType=Event,
                                       │   payload {"__worker__": true, job_id})
                                       │ returns {job_id}
                                       └ fast reads → AgentCore (synchronous)
                                     vaa-api (Lambda, worker role — same function)
                                       │ InvokeAgentRuntime (the long call, ≤900s)
                                       └ writes FINISHED/FAILED → vaa-jobs
                                     AgentCore Runtime (VerticalAdaptionAgent)
                                       └ ECR image → Bedrock
```

One function, one package. `handler.lambda_handler` routes on the event:
a worker event (`__worker__: true`) runs the long AgentCore call; otherwise it
acts as the API. Long actions (`compare`, `generate`, `approve_sync`) use the
job pattern; fast reads (health, industries, versions, sessions, get_pack, …)
are proxied synchronously to AgentCore.

## Resources (account 455162169375, region us-east-1)

| Resource            | Name / ID                                                              |
|---------------------|------------------------------------------------------------------------|
| ECR repo            | `vertical-adaption-agent`                                              |
| AgentCore runtime   | `VerticalAdaptionAgent-Km2rpv6ASP`                                     |
| AgentCore exec role | `VerticalAdaptionAgentCoreRole`                                        |
| DynamoDB jobs       | `vaa-jobs` (TTL on `expires_at`)                                       |
| DynamoDB sessions   | `vaa-sessions`                                                         |
| Lambda (API+worker) | `vaa-api` (role `vaa-lambda-api-role`, timeout 900s, handler `handler.lambda_handler`) |
| HTTP API            | `vaa-http-api` → `https://9knsdjgp3c.execute-api.us-east-1.amazonaws.com` (18 explicit routes) |

Frontend base URL (`frontend/.env`):
`VITE_API_BASE=https://9knsdjgp3c.execute-api.us-east-1.amazonaws.com/api`

## Rebuild & redeploy the agent image

```bash
cd backend
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 455162169375.dkr.ecr.us-east-1.amazonaws.com
docker buildx build --platform linux/arm64 -f agent/Dockerfile \
  -t 455162169375.dkr.ecr.us-east-1.amazonaws.com/vertical-adaption-agent:latest --push .

# Point the runtime at the new image (creates a new version):
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id VerticalAdaptionAgent-Km2rpv6ASP --region us-east-1 \
  --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"455162169375.dkr.ecr.us-east-1.amazonaws.com/vertical-adaption-agent:latest"}}' \
  --role-arn arn:aws:iam::455162169375:role/VerticalAdaptionAgentCoreRole \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"HTTP"}'
```

## Repackage & update the Lambda

```bash
cd backend/lambda
rm -rf vendor function.zip && mkdir vendor
../venv/bin/pip install --target vendor "boto3>=1.40"
(cd vendor && zip -qr ../function.zip . -x "*.dist-info/*" -x "*__pycache__/*")
zip -q function.zip handler.py jobstore.py

aws lambda update-function-code --function-name vaa-api --zip-file fileb://function.zip --region us-east-1
```

## Known gaps

- `POST /api/context-packs/upload-kb` (multipart KB file upload) is not wired
  through API Gateway — it needs a presigned-URL / direct-S3 path. The core
  compare + generate + approve flows are fully wired.
