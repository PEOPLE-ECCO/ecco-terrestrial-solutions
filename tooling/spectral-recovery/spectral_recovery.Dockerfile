FROM ecco-api-base:latest

# Algorithm base path
ENV ALGORITHM_BASE=hatfield.spectral-recovery.src.main
ENV UV_PYTHON=3.12

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON=3.12

# Install actual app depenencies
COPY ./spectral-recovery/requirements.txt requirements.txt
RUN uv pip install --system -r requirements.txt

# Spyndex==0.5.0 relies on the setuptools, which messes with uv
RUN uv pip install --system "setuptools<81"

# Copy sourcecode. Copy models separately to allow for Docker caching
COPY bap/ ./hatfield/bap/
COPY spectral-recovery/ ./hatfield/spectral-recovery/
