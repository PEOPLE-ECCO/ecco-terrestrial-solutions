ARG BASE_IMAGE=ghcr.io/people-ecco/ecco-algorithm-base:1.1.1
FROM ${BASE_IMAGE}

# Algorithm base path
ARG ALGORITHM_BASE="breaks_algo.main"
ENV ALGORITHM_BASE=${ALGORITHM_BASE}
ENV UV_PYTHON=3.12

# metadata
ARG SOLUTION="PEOPLE-ECCO Breaks"
ENV SOLUTION=${SOLUTION}

ARG SOLUTION_VERSION="1.0.1"
ENV SOLUTION_VERSION=${SOLUTION_VERSION}

LABEL org.opencontainers.image.title=${SOLUTION} \
      org.opencontainers.image.description=${SOLUTION} \
      org.opencontainers.image.version=${SOLUTION_VERSION} \
      org.opencontainers.image.authors="Marcos Kavlin-Castaneda <mkavlin@hatfieldgroup.com>" \
      org.opencontainers.image.vendor="Hatfield Consultants" \
      org.opencontainers.image.source="https://github.com/PEOPLE-ECCO/seasonal-sens-slope" \
      org.opencontainers.image.licenses="Apache-2.0"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY tooling/breaks/requirements.txt requirements.txt
RUN uv pip install --system -r requirements.txt

COPY bap/ ./hatfield/bap/
COPY breaks/ ./breaks_algo/