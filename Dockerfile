# Daily supervision pull job (Azure Container Apps Job, cae-cr-eastus).
#
# Same base as lakehouse-data-ingestion/containers/cr_bronze: Linux is what lets
# Container Apps VNet-integrate the job, and the VNet is what puts it behind the
# NAT gateway's static egress IP that CentralReach allowlists.
FROM python:3.11-slim-bookworm

# msodbcsql18 for pyodbc (ACCEPT_EULA is required by the Microsoft package).
# tzdata because the pipeline's month/day logic uses datetime.now(), which must be
# Eastern time, not the container's default UTC.
ENV ACCEPT_EULA=Y
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates tzdata \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && apt-get purge -y curl gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
ENV TZ=America/New_York

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts_notebooks/prod/*.py scripts_notebooks/prod/

# Non-root. The pipeline writes data/ and logs/ under /app (scratch only; nothing
# needs to survive the run), so the runner user has to own it.
RUN useradd --create-home --uid 10001 runner \
 && chown -R runner /app
USER runner

# The scripts use paths relative to scripts_notebooks/prod (../../data).
WORKDIR /app/scripts_notebooks/prod
ENTRYPOINT ["python", "-u", "run_pipeline.py"]
