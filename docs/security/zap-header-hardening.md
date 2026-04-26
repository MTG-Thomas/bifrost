# ZAP Header Hardening Notes

This pass addresses the low-risk passive ZAP findings at the Bifrost client edge (`client/nginx.conf`) while preserving iframe embed support.

## Implemented

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` on normal app routes
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
- `Content-Security-Policy-Report-Only` on normal app routes

The normal security headers are repeated in nginx locations that already set `Cache-Control`; nginx does not inherit parent `add_header` values once a location defines its own.

The HSTS header assumes the public edge is served over HTTPS or behind TLS termination. Browsers ignore HSTS delivered over plain HTTP, so local/self-hosted HTTP compose usage is not pinned by this header.

## Preserved

`/embed` remains iframe-compatible. It does not receive `X-Frame-Options: DENY` or the normal `frame-ancestors 'none'` report-only CSP. The API embed redirect still owns the permissive framing policy for HMAC-authenticated app and form embeds.

## Deferred

- COEP, COOP, and CORP are deferred because cross-origin isolation headers can break embeds, Monaco/syntax-highlighting assets, generated app bundles, or third-party dependencies.
- The current ZAP `Dangerous JS Functions` hit was against the generated syntax-highlighter bundle, so this header pass does not change it. Separately, Bifrost has intentional first-party dynamic evaluation paths in the form expression and app-code runtimes; those should be reviewed in a dedicated sandboxing pass rather than mixed into edge header hardening.
- CSP should remain report-only until browser validation and follow-up ZAP runs show that normal app navigation, auth, WebSockets, generated app bundles, and embeds behave correctly. The first local browser smoke test logged a report-only inline-script violation, so enforcement is intentionally out of scope for this pass.
