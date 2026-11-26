# One Dockerfile for every fake. BACKEND picks which one the image serves:
#
#   docker build --build-arg BACKEND=core -t fake-core .
#
# The names are the keys of BACKENDS in src/fake_backends/server/registry.py.

# Both stages use the same base, so the virtualenv built in the first finds
# the same interpreter in the second. Bump the tag and digest together.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:22eafb370920ad8b429e0aec3fff433e0669968519f5d60469d83729c138b28b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
ARG BACKEND
ARG VERSION=dev
# The port the fake listens on inside the container. Matches DEFAULT_PORT in
# constants.py, which applies when the server runs outside a container.
ARG PORT=8080
# A fixed, unprivileged uid that no base-image user already has.
ARG FAKE_UID=10001
RUN test -n "${BACKEND}" || (echo "build with --build-arg BACKEND=<name>" >&2; exit 1)

LABEL org.opencontainers.image.source="https://github.com/mrsixw/fake-backends" \
      org.opencontainers.image.description="fake-backends: ${BACKEND}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

RUN useradd --system --no-create-home --uid "${FAKE_UID}" fake
COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    FAKE_BACKEND="${BACKEND}" \
    FAKE_HOST=0.0.0.0 \
    FAKE_PORT="${PORT}"

USER fake
EXPOSE ${PORT}

# The slim image has no curl, so the check uses the interpreter it already has.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"FAKE_PORT\"]}/healthz', timeout=2)"]

ENTRYPOINT ["python", "-m", "fake_backends.server"]
