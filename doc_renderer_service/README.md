# Bifrost Document Renderer

This directory owns the source and container image for Bifrost's adjacent PDF
renderer. It exposes the authenticated endpoints consumed by
`bifrost-workspace/modules/document_renderer.py`:

- `POST /render/markdown-pdf`
- `POST /render/html-pdf`
- `GET /health`

The service requires `DOC_RENDERER_API_TOKEN`. Runtime topology, secrets, image
digest promotion, ingress, and Kubernetes resources belong in
`MTG-Thomas/bifrost-infra`; they are intentionally not defined here.

The dedicated GitHub Actions workflow builds the real image and performs a PDF
smoke test for pull requests. Merges to `main` publish `main` and commit-SHA tags
to `ghcr.io/mtg-thomas/bifrost-doc-renderer`. Infra promotion remains an
explicit digest update.
