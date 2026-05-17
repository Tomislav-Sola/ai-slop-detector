FROM python:3.11-slim

WORKDIR /app

# git is occasionally pulled in by chromadb / sentence-transformers extras at install time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy only what's needed to build the package. Keeping this list narrow makes
# the build deterministic regardless of .dockerignore drift.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Pre-bake the all-MiniLM-L6-v2 weights into the image so the first Action
# invocation in a repo does not pay the ~90 MB download cost.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

ENTRYPOINT ["python", "-m", "ai_slop_detector.action_entrypoint"]
