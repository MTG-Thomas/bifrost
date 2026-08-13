FROM node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e

WORKDIR /opt/mcp-conformance

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force
COPY --chown=node:node --chmod=0444 expected-failures.yml ./expected-failures.yml

USER node

ENTRYPOINT ["node", "node_modules/@modelcontextprotocol/conformance/dist/index.js"]
