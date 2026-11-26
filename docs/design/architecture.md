# Architecture

How the shared server core is put together, and where a fake plugs in. The control API and fault-rule format are documented in the [README](../../README.md); the reasons behind the less obvious choices are in the [ADRs](adr/).

## Layout

```
src/fake_backends/            namespace package: no __init__.py
├── server/                   the core every fake image runs (Python 3.14)
│   ├── app.py                create_app(backend): assembles one fake
│   ├── backend.py            Backend base class and RequestInfo
│   ├── control.py            /healthz and the /_fake/ control API
│   ├── middleware.py         FaultMiddleware: the per-request pipeline
│   ├── faults.py             rule parsing, matching and counting
│   ├── journal.py            the request journal
│   ├── store.py              seed state: tokens and backend data
│   ├── serve.py              uvicorn server that can abort connections
│   └── constants.py
└── testing/                  the pytest helper (issue 8; Python 3.11)
```

The split between `server` and `testing` is deliberate: see [ADR 0003](adr/0003-package-split.md).

## A fake is a Backend

Each fake (fake-github, fake-jira, fake-jenkins) is a subclass of `Backend` handed to `create_app()`. It must supply `routes()`. Everything else has a default it can override:

| Method | Default | A fake overrides it to |
|---|---|---|
| `credential(request)` | Bearer or `token` scheme from `Authorization` | read the real service's scheme (for example JIRA basic auth) |
| `is_public(method, path)` | nothing is public | let anonymous calls through where the real API does |
| `unauthorized()` | 401 `{"message": "Bad credentials"}` | send the real service's 401 body |
| `details(request)` | `{}` | expose request facts that fault rules match on and the journal records, such as GraphQL root fields |
| `validate_state(data)` | accept anything | reject a malformed seed with a 400 |
| `supports_hook(spec)` / `hook_action(spec, request)` | no hooks | add service-specific faults, such as a GraphQL `errors[]` entry |

Handlers read seed data from `request.app.state.store.data` and the caller from `request.state.identity`.

## The request pipeline

Requests to `/healthz` and `/_fake/*` go straight to the control routes: they are never faulted, journaled or authenticated. Every other request passes through `FaultMiddleware`:

1. **Read the body** in full, and replay it to the handler later.
2. **Identify the caller.** `backend.credential()` extracts a token, and the seeded `tokens` map turns it into an identity, or `None`.
3. **Get backend details.** `backend.details()` is checked as JSON here, so a bad detail fails on the request that produced it rather than on a later journal read.
4. **Decide the fault.** The fault book counts the request against every matching rule and returns the earliest registered one that fires, if any. See [ADR 0002](adr/0002-faults-before-auth.md).
5. **Journal** the request, including which rule fired.
6. **Act.**
   - No rule: check auth, then call the handler (or answer 401).
   - A rule: apply its action. `delay` and `truncate` still pass through the auth check and handler; the other actions answer on their own.

The middleware is plain ASGI, not Starlette middleware: see [ADR 0004](adr/0004-pure-asgi-middleware.md). `drop` and `truncate` close the connection through `serve.abort_connection()`: see [ADR 0001](adr/0001-connection-abort.md).

## State

One process holds one fake's state in memory: the `Store` (tokens and seed data), the `FaultBook` and the `Journal`. The fault book and journal are lock-protected, so counting and recording stay consistent; the store is replaced in one step by synchronous code on the event loop, so it has no lock. `PUT /_fake/state` replaces the store; `POST /_fake/reset` clears all three. Nothing persists across restarts, so a test suite resets between scenarios rather than restarting the container.

## Testing the core

The tests run a real uvicorn server from `build_server()` on a free port and talk to it over HTTP, through `tests/echo_backend.py`, a placeholder backend that fakes no real API. Running through the real server is what lets the connection-level faults be tested at all.
