# ShipCube web console

The web console for ShipCube: Google sign-in, an optional "link your GCP project" step, the MCP setup guide, and the console screens (apps, app detail with load estimate, caps, access, monitoring and logs; AI providers; API tokens; billing).

Next.js 15 (App Router), React 19, TypeScript and Tailwind CSS 4. Design tokens and rules are in [`docs/design/DESIGN.md`](../../docs/design/DESIGN.md).

Everything runs on **typed mock data** for now. No backend, Google credentials or GCP project is needed.

## Run it

Requires Node 20 or later.

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000. You land on the sign-in page. "Continue with Google" signs you in as a demo user through the mock auth provider. Use **Create an account** to go through onboarding (GCP linking) first.

Other scripts:

```bash
npm run build   # production build
npm run start   # serve the production build
npm run lint    # ESLint (next/core-web-vitals + TypeScript)
```

### Seeing empty and error states

Data pages accept a `state` query parameter so you can check the non-happy paths without a backend:

- `/apps?state=empty`, `/apps?state=error`
- `/billing?state=empty`, `/billing?state=error`
- `/settings/tokens?state=empty`
- `/apps/<id>/access?state=empty`, `/apps/<id>/logs?state=empty`, `/apps/<id>/monitoring?state=empty`

Loading states (skeletons) show on every navigation because the mock API waits 350 ms.

## Layout

```
src/
  app/
    (auth)/            sign-in, sign-up and the server actions behind them
    onboarding/        "Where should your apps run?" step after sign-up
    (console)/         signed-in shell (sidebar) and every console page
      apps/            list; [id]/ overview, access, monitoring, logs
      setup/           MCP setup guide ("Connect your agent")
      settings/        tokens, ai (providers and BYOK), gcp
      billing/
  components/          UI primitives (ui.tsx), charts, code blocks, GCP linker, token creator
  lib/
    auth/              AuthProvider interface + mock adapter
    api.ts             async data functions (mocked); swap for fetch() calls to services/api
    mock-data.ts       typed demo data built on the Olist dataset
    types.ts           shared types the API is expected to return
    mcp.ts             MCP endpoint, scopes, tool list and config snippets
  middleware.ts        redirects to /sign-in when there is no session cookie
```

## Swapping the mocks

**Auth.** Pages only use the `AuthProvider` interface in `src/lib/auth/types.ts` (`getSession`, `signInWithGoogle`, `completeOnboarding`, `signOut`). To use Auth.js, Firebase Auth or Identity Platform, add an adapter next to `mock.ts`, register it in `src/lib/auth/index.ts`, and set `AUTH_PROVIDER`. The mock adapter stores an unsigned session in a cookie and must never run in production.

**Data.** Every page reads through `src/lib/api.ts`. Each function has the shape of the control-plane call it stands in for, so replacing a mock with a `fetch()` to `services/api` doesn't touch the pages.

**MCP details.** The endpoint URL, token scopes and tool list live in `src/lib/mcp.ts`. The backend networking workstream owns the final values.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_PROVIDER` | `mock` | Which auth adapter to use. Only `mock` exists today. |

Never commit `.env` files; `.gitignore` excludes them.
