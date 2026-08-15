"""Cloudflare Workers HTTP entry point for Bifrost Python execution."""

from __future__ import annotations

import secrets

from workers import Response, WorkerEntrypoint

from runtime import execute_payload


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        expected = str(self.env.EXECUTOR_TOKEN)
        supplied = request.headers.get("Authorization") or ""
        if not secrets.compare_digest(supplied, f"Bearer {expected}"):
            return Response.json({"detail": "unauthorized"}, status=401)
        if request.method != "POST":
            return Response.json({"detail": "method not allowed"}, status=405)

        try:
            payload = await request.json()
        except Exception:
            return Response.json({"detail": "invalid JSON"}, status=400)
        if not isinstance(payload, dict):
            return Response.json({"detail": "request body must be an object"}, status=400)
        return Response.json(await execute_payload(payload))
