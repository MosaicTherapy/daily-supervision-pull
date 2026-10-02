# Azure deployment (Container Apps Job)

Runs the daily pipeline in Azure instead of on a laptop's Task Scheduler. It follows
the pattern of the `cr_bronze` and `waystar_pull` jobs in `lakehouse-data-ingestion`:

```
image      mosaicdataacr.azurecr.io/daily-supervision-pull:v1   (Dockerfile at repo root)
job        job-daily-supervision-pull in cae-cr-eastus (definition: job-daily-supervision-pull.json)
identity   id-cr-bronze (AcrPull, Key Vault Secrets User)
secrets    intakeApp-nightly-runs: cr-server, cr-user, cr-password, gmail-email,
           gmail-app-password, google-drive-oauth-token
cron       0 11 * * *  (UTC)
```

`cae-cr-eastus` matters: its subnet sits behind `nat-cr-eastus`, whose static egress
IP is the one CentralReach allowlists. The job would not reach the CR DWH from
anywhere else.

## How it differs from the laptop run

- **Drive publish goes through the API.** With `GOOGLE_OAUTH_TOKEN_JSON` set,
  `merge_data.publish_to_google_drive` uses `drive_api.py` instead of the `G:/`
  mount. Same behaviour: other workbooks move to `archived/`, then the new one is
  uploaded. It publishes as dcox@ using that user's OAuth refresh token, because
  the tracker folder is in a user's My Drive and service accounts have no Drive
  storage of their own.
- **A failed publish fails the run** (both paths), so the email reports failure
  instead of success with nothing published.
- **No state between runs.** `data/` and `logs/` live on the container's scratch
  disk and vanish afterwards. Nothing depends on them: the archive lookup in
  `run_pipeline.py` doesn't change the output filename, and the employee-locations
  cache only skips a query. Logs go to the environment's Log Analytics workspace.
- **Code ships by image, not by `git pull`.** Merging to `main` changes nothing until
  a new image is built and the job is pointed at it (see "Deploying a change").
- **Schedule is UTC.** 11:00 UTC is 07:00 EDT and 06:00 EST.

## One-time setup

### 1. OAuth client (Google Cloud project `mosaic-data-pipelines`)

The project already exists with the Drive API enabled. In the Cloud Console:

1. Google Auth Platform > **Branding / Audience**: user type **Internal** (Mosaic
   accounts only; Internal apps' refresh tokens don't expire on a 7-day testing
   clock the way External ones do).
2. **Clients** > Create client > Application type **Desktop app** >
   download the client JSON.

### 2. Sign in once and store the token

```
pip install google-auth-oauthlib
python deploy/authorize_drive_user.py <client.json> <token.json>
az keyvault secret set --vault-name intakeApp-nightly-runs --name google-drive-oauth-token --file <token.json>
```

Sign in as dcox@ when the browser opens. The script prints the tracker folder's
contents to prove the token works. Then delete both local JSON files.

The token stops working if the user revokes the app (myaccount.google.com >
Security > third-party access) or the account is suspended. Rerun step 2 to
replace it.

### Alternative: service account with domain-wide delegation

Not tied to one user's consent, but needs a Workspace super admin. Already
prepared: service account
`daily-supervision-pull@mosaic-data-pipelines.iam.gserviceaccount.com`
(client ID `114504728844766821674`), key in Key Vault as `google-drive-sa-json`.
To switch: admin adds the client ID with scope `https://www.googleapis.com/auth/drive`
under Security > API controls > Manage Domain Wide Delegation; then in the job,
replace the `GOOGLE_OAUTH_TOKEN_JSON` env with `GOOGLE_SERVICE_ACCOUNT_JSON`
(secret `google-drive-sa-json`) plus `GOOGLE_IMPERSONATE_USER=dcox@mosaictherapy.com`.

### 3. Confirm the existing secrets match this pipeline

The job reuses the `cr-*` and `gmail-*` secrets the other jobs use. `cr-server`
must be the same host as `CR_DWH_SERVER` in the local `.env`, and `gmail-email`
must be the account `gmail-app-password` belongs to.

### 4. Build and create the job

```
az acr build --registry mosaicdataacr --image daily-supervision-pull:v1 --no-logs .
az containerapp job create -g cr-nightly-pulls -n job-daily-supervision-pull --yaml deploy/job-daily-supervision-pull.json
```

## Test before cutover

The job definition points at the real tracker folder. For the first runs, point it at
a scratch Drive folder (with its own `archived` subfolder) straight after creating it:

```
az containerapp job update -g cr-nightly-pulls -n job-daily-supervision-pull --set-env-vars DRIVE_LIVE_FOLDER_ID=<scratch-id> DRIVE_ARCHIVE_FOLDER_ID=<scratch-archived-id>
az containerapp job start -g cr-nightly-pulls -n job-daily-supervision-pull
az containerapp job execution list -g cr-nightly-pulls -n job-daily-supervision-pull -o table
```

Leave it on the scratch folder for a few scheduled runs, including one on days
1-5 if possible (that path also publishes the previous month's FINAL file to
`archived/`). Compare each scratch workbook with the laptop's. Expect one extra
pipeline email a day while both run.

## Cutover

```
az containerapp job update -g cr-nightly-pulls -n job-daily-supervision-pull --set-env-vars DRIVE_LIVE_FOLDER_ID=1Mh9gqV27KkEEuyX6M35_SB_vTErRz7Gm DRIVE_ARCHIVE_FOLDER_ID=1w66zw3i_-tQ9AOQlzN6pwNU8quXhsRn1
```

Then on the laptop, disable (don't delete yet) the `DailySupervisionPull` scheduled
task. Delete it after a couple of weeks of clean Azure runs.

## Deploying a change

Bump the tag, build, and point the job at it:

```
az acr build --registry mosaicdataacr --image daily-supervision-pull:v2 --no-logs .
az containerapp job update -g cr-nightly-pulls -n job-daily-supervision-pull --image mosaicdataacr.azurecr.io/daily-supervision-pull:v2
```

Build from `main`, which is what the laptop job ran.
