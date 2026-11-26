# ADR 0001: Abort connections through a uvicorn protocol subclass

- **Status:** Accepted
- **Date:** 2026-11-26
- **Issue:** [issue 6](https://github.com/mrsixw/fake-backends/issues/6)

## Context

Two fault actions have to misbehave at the connection level, because that is what the consumers' retry and offline paths must survive:

- `drop` closes the connection before any response.
- `truncate` sends the headers and part of the body, then closes.

ASGI gives an application no way to do either. An app can only send messages; the server decides what happens to the socket. Raising an exception mid-response makes uvicorn log an error and close, but before the response starts it sends a 500 instead, which is not a dropped connection.

## Decision

Run every fake on `AbortableH11Protocol`, a subclass of uvicorn's h11 protocol, built by `serve.build_server()`.

- The subclass overrides only `data_received`, the standard `asyncio.Protocol` method. It sets a ContextVar to the connection's transport around the call to the parent.
- uvicorn creates each request's task inside `data_received`, and an asyncio task copies the context it is created in. The request task therefore sees its own transport.
- `abort_connection()` reads the ContextVar, calls `transport.abort()`, then yields once so uvicorn's `connection_lost` runs and it stops trying to answer.
- The server is configured with `reset_contextvars=False`. With a fresh context per task, the request would not see the transport.
- If the transport is missing (the app is run some other way), `abort_connection()` raises `ConnectionAbortUnavailable` with a message saying to use `build_server()`.

## Alternatives rejected

- **Raise from the app.** Gives a 500 rather than a drop, and logs a server error for every intended fault.
- **Patch uvicorn internals**, such as the cycle object's transport. More fragile than overriding a public protocol method.
- **A proxy in front of the fake** that cuts connections. Another process per image, and the proxy would have to understand the fault rules.
- **Switching to another ASGI server.** It would need the same kind of hook into its connection handling, and uvicorn is otherwise the simplest fit.

## Consequences

- The fakes depend on two uvicorn behaviours that are not documented as guarantees: requests are scheduled from inside `data_received`, and `reset_contextvars=False` keeps the inherited context. `pyproject.toml` requires `uvicorn>=0.54`, the version this was built and tested on, and says why next to the bound.
- **A uvicorn upgrade that changes either behaviour fails CI.** `test_drop_closes_the_connection_before_any_response` fails if the abort does nothing, and also fails if `reset_contextvars` is turned on (both checked by deliberately breaking the code).
- The truncate tests do not guard this on their own: uvicorn closes an incomplete response itself, so they pass even with the abort disabled. The drop test is the guard.
- Only the h11 protocol is supported. Running with `httptools` loses `drop` and `truncate`, which fail loudly rather than silently.
