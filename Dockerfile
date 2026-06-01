FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        ccxt>=4.4 pandas>=2.2 numpy>=2.0 pyarrow>=18.0 pyyaml>=6.0 \
        python-dotenv>=1.0 aiohttp>=3.10 websockets>=13.0 rich>=13.9 \
        sqlalchemy>=2.0 aiosqlite>=0.20 greenlet>=3.0 certifi>=2024.0

COPY src ./src
COPY scripts ./scripts
COPY config ./config
COPY main.py ./

RUN mkdir -p data/paper_trading data/poly_klines_live logs

CMD ["python", "main.py", "paper-trade-poly"]
