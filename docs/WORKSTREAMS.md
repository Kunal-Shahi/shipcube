# Workstreams

Each workstream runs in its own Claude Code session, on its own branch, and opens a PR to `main`. Only touch the paths you own. To change a shared file (`README.md`, `docs/ARCHITECTURE.md`), put the proposed change in your PR description instead of editing the file.

| Workstream | Owns | Delivers |
|---|---|---|
| **Frontend** | `apps/web/**`, `docs/design/**` | Design system; sign-up and sign-in (Google); MCP setup guide; app list and detail, access, caps, monitoring screens (mock data is fine) |
| **Backend networking** | `docs/research/networking.md`, `services/mcp/**`, `services/api/**` (design and skeleton) | MCP transport and auth; control-plane API; request flow from agent → MCP → API → GCP; how user apps are reached (domains, ingress, proxy) |
| **Infra and security** | `docs/research/security-tenancy.md`, `docs/research/database-design.md`, `infra/**`, `services/gateway/**` (design) | Who can access what; Google SSO; linking a user's own GCP project; secrets; the database schema that follows from all of this |
| **Language trade-offs** | `docs/research/language-tradeoffs.md`, `research/bench/**` | Go vs Python 3 for each component, with small benchmarks and a recommendation weighted by how fast we can ship in 10 days |

## Shared decisions (already made)

- MCP-first: users build apps in their own coding agent and connect to ShipCube's MCP server. The web console handles accounts, access, caps and monitoring.
- Cloud: GCP. ShipCube's project runs on $300 of free credits. Users can link their own GCP project with Google sign-in and use their own credits.
- Identity: Google sign-in. Apps are private by default; only the owner and the people they allow can open them.
- Meta models are required: Muse Spark for load estimation and placement reasoning, Muse Code as the demo coding agent, and Muse as the default model in the AI gateway for user apps. BYOK keys work for OpenAI-compatible providers, OpenAI and Anthropic. Claude subscriptions are not supported, because Anthropic doesn't allow third-party products to use claude.ai login.
- Public repo. Never commit secrets, real customer data or anything from an employer's codebase.

## Deadline

Submissions close **Nov 4, 9:00 PM ET**. Research PRs should land by **Oct 29** so building can start on decisions.
