# Build for linux/amd64 (the course host is Linux aarch64):
#   docker buildx build --platform linux/amd64 -t claims-intake:day4 .
#
# StubPolicyClient reads data/policies.json via Path(__file__).parents[2] / "data".
# PYTHONPATH=/app/src makes `claims` load from the copied tree so that path is /app/data.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "claims.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
