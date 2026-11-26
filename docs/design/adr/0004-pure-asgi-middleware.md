# ADR 0004: The fault layer is plain ASGI middleware

- **Status:** Accepted
- **Date:** 2026-11-26
- **Issue:** [issue 6](https://github.com/mrsixw/fake-backends/issues/6)

## Context

The fault layer wraps every non-control request. Starlette offers `BaseHTTPMiddleware`, which is simpler to write: it receives a `Request` and returns a `Response`. But two fault actions need more than a request-to-response function:

- `drop` must never produce a response at all.
- `truncate` must forward the real handler's response start and part of its body as they are produced, then stop the handler and close the connection, even when the body never ends.

## Decision

`FaultMiddleware` is a plain ASGI callable (`scope, receive, send`). It:

- reads the request body once and replays it to the handler through a wrapped `receive`;
- for `truncate`, passes the handler a wrapped `send` that forwards messages until `keep_bytes` are sent, then raises a private exception to stop the handler, and aborts the connection ([ADR 0001](0001-connection-abort.md));
- for `drop`, aborts the connection without calling the handler.

## Why not BaseHTTPMiddleware

- It wraps the handler's response in its own streaming machinery, so the middleware cannot control exactly which bytes reach the socket, or stop part-way and close.
- It runs the handler in a separate task, and context-variable changes do not flow back out of it. The connection abort depends on context variables reaching the request task, so an extra layer of task handling is a risk with no benefit here.

## Consequences

- The middleware is lower level and longer than a `BaseHTTPMiddleware` version would be.
- Truncation streams rather than buffers: an endless response is cut as promptly as a short one (`test_truncate_cuts_an_endless_stream_without_buffering_it`).
- Fakes do not need to know about any of this. They write ordinary Starlette handlers, and every fault applies to them unchanged.
