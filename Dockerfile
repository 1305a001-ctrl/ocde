FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.11-slim

LABEL org.opencontainers.image.source=https://github.com/1305a001-ctrl/ocde
LABEL org.opencontainers.image.description="Oracle Confidence Divergence Engine"

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
 && rm -rf /wheels

# Run as non-root
RUN useradd --create-home --shell /bin/bash ocde
USER ocde

EXPOSE 8014

CMD ["python", "-m", "ocde.main"]
