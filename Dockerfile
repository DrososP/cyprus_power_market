# Cyprus power market dashboard.
#
# One image serves two roles, selected by the compose service that runs it:
# the Streamlit app, and the scheduler that refreshes the data. They share the
# same code and the same volume, so there is no way for the two to drift.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Nicosia

# tzdata so the container agrees with TSOC about what "today" means; curl for
# the healthcheck. No build toolchain — every wheel here is prebuilt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so a code change doesn't invalidate the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY .streamlit ./.streamlit
COPY refresh.sh scheduler.sh ./
RUN chmod +x refresh.sh scheduler.sh

# data/ is a mounted volume in compose; this is only the fallback for a bare
# `docker run` so the app starts instead of erroring on a missing directory.
RUN mkdir -p data/series data/raw data/tidy data/parquet

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "dashboard.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
