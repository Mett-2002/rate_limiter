# Rate limiter

An internal Django REST Framework API for limiting requests per user. The default rule is **5 accepted requests in a rolling 60-second window**. Each API client can have its own limit and window.

## Algorithm

The **exact sliding-window log** stores accepted timestamps in a Redis FIFO list for each API client/user pair. Every consume or status check runs one Lua script:

1. Remove timestamps outside the window.
2. Count the remaining entries.
3. For a consume request, add a timestamp if capacity is available; otherwise reject it.

The interval is `(now - window, now]`. A request accepted at `12:00:00` stops counting at exactly `12:01:00` under the default rule. Each request frees its own slot. Rejections do not extend the window; status checks clean up expired entries without adding one.

Redis supplies the clock and runs the script atomically, so concurrent calls cannot claim the same last slot. Idle lists expire automatically. Removing `k` old entries costs `O(k + 1)`; each active list stores at most the configured limit.

## Design

- `rate_limiter_api/` contains the API, client configuration, Swagger UI and engine tests.
- `demo_website_using_rate_limiter/` is a separate Django caller with Alice/Bob controls and an animated timeline.
- SQLite stores client settings and key hashes. Redis stores request history.

A gateway or microservice calls the limiter with its Bearer key and a verified `user_id`. It proceeds only when the request is allowed. Check each action at one place to avoid counting it twice.

Quotas are separate for each client/user pair. The demo uses the `local-demo` client; a different client key in Swagger has a separate quota. Changing a client's rule starts fresh quota. Rotating its key preserves quota.

## First-time setup

Requires **Python 3.11** and a separate **Redis server**. The Python `redis` package does not install that server.

These commands use Windows CMD, starting in the repository root. Run one command at a time. Setup is only needed once.

```cmd
cd rate_limiter_api
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py createsuperuser
.venv\Scripts\python.exe scripts\prepare_demo.py
cd ..\demo_website_using_rate_limiter
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The helper creates the demo client and saves its key in the demo's ignored `.runtime/credentials.json`. The key stays in the demo backend.

## Start the servers

Open three CMD terminals in the repository root and leave them running.

**1. Redis** — run from its installation folder if `redis-server` is not on PATH:

```cmd
redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no
```

**2. API:**

```cmd
cd rate_limiter_api
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8200
```

**3. Demo:**

```cmd
cd demo_website_using_rate_limiter
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8300
```

PowerShell users can use `.\run.ps1` inside each project instead. The default Redis URL is `redis://127.0.0.1:6379/0`; set `REDIS_URL` before starting the API to use another address.

## Try the API

Open [Django Admin](http://127.0.0.1:8200/admin/), create an API client and copy its key. Open [Swagger UI](http://127.0.0.1:8200/api/docs/), click **Authorize**, paste the key without `Bearer`, then use **Try it out** and **Execute**.

| Request | Input | Result |
|---|---|---|
| POST `/api/limits/consume/` | JSON: `{"user_id":"alice"}` | 200 allowed; 429 full |
| GET `/api/limits/status/` | Query: `user_id=alice` | 200 quota snapshot; consumes nothing |
| GET `/api/health/` | None | 200 Redis available; 503 unavailable |

Consume and status require `Authorization: Bearer YOUR_API_KEY`. Invalid input returns 400; invalid credentials 401. Rejections include `Retry-After`. On 503, the calling service must stop the action.

The [demo](http://127.0.0.1:8300/) shows requests sent from that page: green for accepted, red for rejected. Its plotted times are approximate; quota comes from the API.

## Tests

With Redis running, from `rate_limiter_api/`:

```cmd
.venv\Scripts\python.exe manage.py test limiter
```

`limiter/test_engine.py` covers limits, exact boundaries, individual expiry, client/user isolation and concurrency using real Redis. Set `TEST_REDIS_URL` to change its default Redis database, `redis://127.0.0.1:6379/15`. Cleanup deletes only each test's keys.

From the demo folder, `.venv\Scripts\python.exe manage.py test support` tests the views and HTTP client with mocked API calls. There is no automated HTTP suite for the limiter API.

## Scope

This is a local development setup. The Redis command above disables persistence, so restarting it clears quotas. An accepted call consumes a slot even if the caller's later action fails. POST requests are not automatically retried.
