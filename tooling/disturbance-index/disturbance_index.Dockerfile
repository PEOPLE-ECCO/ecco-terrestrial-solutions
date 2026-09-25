ARG BASE_IMAGE=ghcr.io/people-ecco/ecco-algorithm-base:1.1.1
FROM ${BASE_IMAGE}

# Algorithm metadata
ARG ALGORITHM_BASE="disturbance_index"
ENV ALGORITHM_BASE=${ALGORITHM_BASE}
ENV UV_PYTHON=3.12

ARG SOLUTION="PEOPLE-ECCO Disturbance Index"
ENV SOLUTION=${SOLUTION}

ARG SOLUTION_VERSION="1.0.2"
ENV SOLUTION_VERSION=${SOLUTION_VERSION}

LABEL org.opencontainers.image.title=${SOLUTION} \
      org.opencontainers.image.description=${SOLUTION} \
      org.opencontainers.image.version=${SOLUTION_VERSION} \
      org.opencontainers.image.authors="Barry Pierce <bpierce@hatfieldgroup.com>" \
      org.opencontainers.image.vendor="Hatfield Consultants" \
      org.opencontainers.image.source="https://github.com/PEOPLE-ECCO/ecco-terrestrial-solutions" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update && \
    apt-get install -y \
    binutils libproj-dev gdal-bin g++

# Install Python dependencies
COPY tooling/disturbance-index/requirements.txt /tmp/requirements.txt
RUN uv pip install --system -r /tmp/requirements.txt

# Copy workflow assets
WORKDIR /app
COPY VDO_disturbance_index/disturbance_index_cwl.py /app/disturbance_index_cwl.py
COPY VDO_disturbance_index/disturbance_integration.py /app/disturbance_integration.py
COPY VDO_disturbance_index/disturbance_index-job.yml /app/disturbance_index-job.yml

ENTRYPOINT ["python", "/app/disturbance_index_cwl.py"]
CMD ["--help"]
