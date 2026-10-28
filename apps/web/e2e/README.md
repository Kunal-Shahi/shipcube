# Console end-to-end tests

Playwright drives one person's path through the console against a running local stack:

1. Sign in. With the Firebase Auth emulator this is the real popup → ID token → session cookie flow; with the `DEV_MODE` mock sign-in there is no popup.
2. Create a workspace PAT on the setup page, and check that it works against the API.
3. Check that the setup guide shows this stack's MCP URL and the new token.
4. Find the app `make smoke` deployed in the apps list.
5. Share it with a person, through the console. The API's access list must show the grant.
6. Lower its spending cap, through the console. The API's caps must match.
7. Delete it the way an agent does (`DELETE /v1/apps/<app>` with the PAT), then approve the deletion on the console's approval page.
8. Restore it from the console. This also leaves the stack ready for another run.

`sign-in-redirect.spec.ts` covers sign-in where a popup can't work: popups blocked, and a browser that opens the sign-in window in the same tab. Both must finish through the redirect flow. These tests skip unless the console runs `AUTH_PROVIDER=firebase-emulator`.

The tests start nothing themselves. The stack must already be up, and `make smoke` must have deployed the app.

## Run

```bash
cd apps/web
npx playwright install chromium   # once
npm run test:e2e
```

| Variable | Default | Meaning |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:3000` | The console |
| `E2E_API_URL` | `http://127.0.0.1:8080` | The API, as the test runner reaches it |
| `E2E_MCP_URL` | `http://localhost:8080/mcp` | What the setup guide must show. Same value as the console's `SHIPCUBE_MCP_URL` |
| `E2E_APP` | `olist-tracker` | The app `make smoke` deploys (`services/mcp/scripts/smoke.py`) |
| `E2E_EMAIL` | `e2e-<timestamp>@example.com` | The emulator account to sign in as |

## The console in the local stack

Run the console with these settings:

```bash
DEV_MODE=1
AUTH_PROVIDER=firebase-emulator              # or mock, the fallback without the emulator
FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099   # the emulator as the console server reaches it
FIREBASE_AUTH_EMULATOR_PUBLIC_URL=http://127.0.0.1:9099   # as the browser reaches it, if different
SHIPCUBE_API_URL=http://127.0.0.1:8080
SHIPCUBE_MCP_URL=http://localhost:8080/mcp
SHIPCUBE_DEV_API_SESSION=<the API's SHIPCUBE_DEV_CONSOLE_TOKEN>
```

The API can't verify the emulator's unsigned tokens, so the console calls it with the dev console session. That session belongs to the API's bootstrap user (`SHIPCUBE_DEV_CONSOLE_EMAIL`), which must be in the workspace `make smoke` deployed to.

Outside `DEV_MODE`, the console refuses `AUTH_PROVIDER=firebase-emulator`, and it refuses `FIREBASE_AUTH_EMULATOR_HOST` with any provider. On Cloud Run it refuses both, even with `DEV_MODE`.

## When it fails

Failures keep a trace, a screenshot and a page snapshot under `test-results/`. Open the trace with `npx playwright show-trace test-results/<test>/trace.zip`.

The emulator's sign-in popup hands its result to a relay iframe in the console page, and that iframe loads `https://apis.google.com/js/api.js`. If that load stalls, the attempt is dropped and sign-in is retried, up to 3 times. The runner therefore needs internet access.
