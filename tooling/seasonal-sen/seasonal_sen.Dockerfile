FROM ecco-api-base:latest

ENV ALGORITHM_BASE=seasonal_sen.src.main
ENV UV_PYTHON=3.12

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY ./tooling/seasonal-sen/requirements.txt requirements.txt
RUN uv pip install --system -r requirements.txt

COPY seasonal-sen/ ./seasonal_sen/
