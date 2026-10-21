# Lambda — AgentCore trigger

API Gateway invokes `handler.lambda_handler`, which forwards the request to the
deployed AgentCore Runtime via `bedrock-agentcore:InvokeAgentRuntime`.

## Contract

- Input: API Gateway proxy event (`httpMethod`, `path`, JSON `body`).
- The handler maps `(method, path)` → an agent `action` + `payload`
  (see `_resolve_action`), matching the routes in `app/routes/`.
- Output: API Gateway proxy response; the agent's JSON is passed through.

## Environment

| Variable            | Purpose                                            |
|---------------------|----------------------------------------------------|
| `AGENT_RUNTIME_ARN` | ARN of the deployed AgentCore runtime (required)   |

## IAM (least privilege)

The Lambda execution role needs:

```json
{
  "Effect": "Allow",
  "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
  "Resource": "<AGENT_RUNTIME_ARN>"
}
```

## Packaging

```bash
pip install -r requirements.txt -t build/
cp handler.py build/
cd build && zip -r ../lambda.zip .
```

> No AWS resources are created yet. Provisioning (ECR, AgentCore runtime,
> Lambda, API Gateway) happens later via the CLI when you're ready.
