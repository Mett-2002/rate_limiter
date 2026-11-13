# Rate limiter

A Django REST Framework service that allows each user five accepted requests in any rolling 60-second window. The limit and window are configurable per API client.

## Algorithm

The **sliding-window log** uses a Redis FIFO list for each API client/user pair. On every consume or status request, one Lua script:

1. Reads Redis time.
2. Removes timestamps at least one window old.
3. Counts the remaining timestamps.
4. For a consume request, appends the current timestamp if capacity remains; otherwise rejects it.

The window is `(now - 60 seconds, now]`. A request accepted at `12:00:00` stops counting at exactly `12:01:00`. Each accepted request releases its own slot. Rejections do not start a new timeout.

Lua makes the check and append atomic, preventing simultaneous requests from claiming the same last slot. Rejections and status checks neither append nor extend expiry. Redis expires idle lists automatically; individual old timestamps are removed when the script runs. No scheduled cleanup is needed.

Removing `k` expired entries costs `O(k + 1)`. Storage is `O(limit)` per active bucket. The implementation is in `rate_limiter_api/limiter/sliding_window.lua`; `services.py` handles Redis connections and results.

## System design

- **rate_limiter_api:** authenticates callers, validates input and returns quota decisions.
- **demo_website_using_rate_limiter:** a separate Django website that calls the API over HTTP. It has a request button, user selector and animated timeline. It does not persist requests.
- **SQLite:** stores API-client settings, key hashes and policy versions.
- **Redis:** stores accepted timestamps, separated by client, policy version and user ID hash.

An administrator creates clients and generates keys in Django Admin. Callers send `Authorization: Bearer YOUR_API_KEY`; only the SHA-256 hash is stored. The demo keeps its key in the backend.

The calling backend must supply a trusted user ID and proceed with its business action only after an allowed decision. The limiter does not forward requests. Changing a rule through Admin/model save starts fresh quota; rotating a key preserves quota.

## API

Consume and status require the Bearer key. Consume accepts only `{"user_id":"alice"}`.

| Method | Path | Response |
|---|---|---|
| POST | `/api/limits/consume/` | 200 accepted; 429 rejected |
| GET | `/api/limits/status/?user_id=alice` | 200 snapshot; no slot consumed |
| GET | `/api/health/` | 200 Redis available; 503 unavailable |

Decisions include `allowed`, `used`, `remaining` and `retry_after`. A rejected consume includes `Retry-After`. Invalid input returns 400; invalid credentials 401. Redis failure returns 503 and the caller must stop the action.

## Run locally

Requires Python 3.11 and a running Redis server. The default Redis address is `redis://127.0.0.1:6379/0`; set `REDIS_URL` for another instance. The Python `redis` package is a client, not the Redis server.

From the repository root, in PowerShell:

```powershell
cd rate_limiter_api
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\run.ps1
```

Swagger UI: http://127.0.0.1:8200/api/docs/ (the root and old playground URLs redirect here). Create clients in Django Admin, click **Authorize**, and paste the key without the `Bearer` prefix. Expand an endpoint, choose **Try it out**, enter the user ID and **Execute**. Swagger displays the actual status, headers and JSON. The OpenAPI schema is at `/api/schema/`. UI assets are bundled locally; no CDN is needed, and authorization is not saved across reloads.

For the demo, use another terminal from the repository root:

```powershell
.\rate_limiter_api\.venv\Scripts\python.exe rate_limiter_api\scripts\prepare_demo.py
cd demo_website_using_rate_limiter
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run.ps1
```

Open http://127.0.0.1:8300/. The setup helper reuses valid demo credentials or generates a replacement. They are stored in an ignored local file.

For an existing checkout, reinstall `rate_limiter_api/requirements.txt` in its virtual environment and restart the API after updating.

## Tests

From the API folder, with Redis running:

```powershell
.\.venv\Scripts\python.exe manage.py test limiter --verbosity 2
```

`limiter/test_engine.py` covers request limits, time boundaries, expiry, user/client isolation and concurrent calls against real Redis. Test keys use random prefixes and are cleaned up individually. HTTP responses and authentication can be checked manually in Swagger UI; this project has no separate HTTP test suite.

From the demo folder, run `.\.venv\Scripts\python.exe manage.py test support`. These tests mock the HTTP connection to the API.

## Scope

One policy per API client. Redis restart behavior depends on its persistence settings. An accepted POST consumes quota even if the later business action fails. There is no idempotency or automatic POST retry. This configuration is for local development.
