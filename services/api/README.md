# services/api

The ShipCube control-plane API. It owns all state (apps, operations, caps, access, data sources) and drives GCP. The MCP server and the web console are both clients of it.

- **Contract:** [`openapi.yaml`](openapi.yaml). This is the stable part.
- **Implementation:** `src/` is a TypeScript skeleton with in-memory mock state. Operations advance with wall-clock time so that polling and progress behave like a real deploy. It is meant to be replaced: if the language trade-off research picks Go or Python for the control plane, rewrite `src/` against the same OpenAPI file, and the MCP server and console won't notice.
- **Design:** `docs/research/networking.md` §3 (REST, versioning, the job model) and §4 (request flow).

```bash
npm install && npm start          # http://127.0.0.1:8081
curl -s localhost:8081/v1/me
curl -s -XPOST localhost:8081/v1/apps/demo/deployments -H 'Idempotency-Key: k1' \
  -d '{"source":{"kind":"git","url":"https://github.com/example/app"}}'
```

Set `SHIPCUBE_DEV_TOKEN` to require `Authorization: Bearer <token>` on every route except `/healthz`.
