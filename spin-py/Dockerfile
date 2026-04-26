FROM python:3.14-slim

# Install uv from PyPI to avoid GHCR pull/auth requirements
RUN pip install --no-cache-dir --upgrade pip uv

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

# Copy only the dependency files first
# This ensures that 'uv sync' only runs when these files change
COPY pyproject.toml uv.lock ./

# Install dependencies 
# --frozen: ensures uv doesn't update the lockfile during build
# --no-install-project: avoids erroring if the source code isn't there yet
RUN uv sync --frozen --no-cache --no-install-project --extra container

COPY football_core.py container_server.py ./

EXPOSE 3000

CMD ["uvicorn", "container_server:app", "--host", "0.0.0.0", "--port", "3000"]