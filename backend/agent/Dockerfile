# AgentCore Runtime image for the Vertical Adaptation Agent.
#
# AgentCore requires:
#   * linux/arm64 architecture
#   * the container to listen on port 8080 and serve POST /invocations + GET /ping
#     (both provided by bedrock_agentcore's app.run()).
#
# Build context is the `backend/` directory so the `agent/` package is copied as
# a top-level package (imports are `agent.*`). Build with:
#   docker buildx build --platform linux/arm64 -f agent/Dockerfile -t vaa-agent .
FROM --platform=linux/arm64 python:3.11-slim

# Avoid interactive prompts; keep Python output unbuffered for CloudWatch.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_MODE=local

WORKDIR /app

# Install agent dependencies first (better layer caching).
COPY agent/requirements.txt /app/agent/requirements.txt
RUN pip install --no-cache-dir -r /app/agent/requirements.txt

# Copy only the agent package (the FastAPI `app/` layer is NOT part of this image).
COPY agent/ /app/agent/

# AgentCore invokes POST /invocations and health-checks GET /ping on 8080.
EXPOSE 8080

# Start the AgentCore runtime server.
CMD ["python", "-m", "agent.runtime"]
