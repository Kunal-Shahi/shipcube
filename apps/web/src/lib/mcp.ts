// MCP connection details shown on the setup guide. They follow the decided MCP shape
// (docs/WORKSTREAMS.md, "Decided") and docs/research/networking.md from the networking
// workstream. Change them here if those documents change. The endpoint itself comes from
// SHIPCUBE_MCP_URL at runtime (lib/server/config.ts) and is passed in, never hard-coded.

export const TOKEN_ENV = "SHIPCUBE_TOKEN";

export const SCOPES = [
  { id: "apps:read", label: "See apps, logs and metrics", detail: "list_apps, inspect_app, operation" },
  { id: "apps:deploy", label: "Estimate, deploy and delete apps", detail: "create_upload, estimate_load, deploy_app, delete_app" },
  { id: "caps:write", label: "Change caps", detail: "set_caps" },
  { id: "secrets:write", label: "Choose the AI provider and save provider keys", detail: "ai_config" },
  { id: "access:write", label: "Change who can open apps", detail: "set_access" },
  { id: "data:read", label: "Read the data model and query it", detail: "data_list_cubes, data_describe_cube, data_query, data_search_values" },
  { id: "data:migrate", label: "Propose data model changes", detail: "data_propose_migration" },
] as const;

export type ToolGroup = "Apps" | "Data";

export const TOOLS: { name: string; group: ToolGroup; purpose: string; prompt: string; approval?: string }[] = [
  { name: "whoami", group: "Apps", purpose: "Returns your account, workspace, token permissions and whether a GCP project is linked.", prompt: "Which ShipCube workspace am I in?" },
  { name: "create_upload", group: "Apps", purpose: "Returns a short-lived upload URL. The agent packages your project and uploads it with curl.", prompt: "(called for you before a deploy)" },
  { name: "estimate_load", group: "Apps", purpose: "Profiles the code in a sandbox. The classifier returns the workload class, tier, resources and cost; Muse Spark explains why and suggests caps.", prompt: "How much would this cost to run?" },
  { name: "deploy_app", group: "Apps", purpose: "Builds and deploys to the chosen tier. Returns an operation to follow.", prompt: "Deploy this with ShipCube", approval: "First deploy to your own GCP project" },
  { name: "operation", group: "Apps", purpose: "Checks or cancels a running deploy, estimate or delete.", prompt: "Is the deploy done yet?" },
  { name: "list_apps", group: "Apps", purpose: "Lists your apps with health, tier, URL and spend.", prompt: "What's running right now?" },
  { name: "inspect_app", group: "Apps", purpose: "Status, logs and metrics for one app, in one call.", prompt: "Why is the scorecard returning 503s?" },
  { name: "set_caps", group: "Apps", purpose: "Sets spend, request-rate, concurrency, instance and token limits.", prompt: "Cap it at $20 a month", approval: "Raising a cap above the workspace default" },
  { name: "set_access", group: "Apps", purpose: "Adds or removes people, Google groups or domains, and returns the full access list.", prompt: "Share it with finance-team@example.com", approval: "Sharing with a whole domain" },
  { name: "ai_config", group: "Apps", purpose: "Chooses the model provider the app uses through the AI gateway: Muse by default, or one of your keys.", prompt: "Use our Fireworks key for the triage app" },
  { name: "delete_app", group: "Apps", purpose: "Deletes an app. It can be restored from the console for 7 days.", prompt: "Delete the old ops report", approval: "Always" },
  { name: "data_list_cubes", group: "Data", purpose: "Lists the cubes (tables of business meaning) in your data model.", prompt: "What data can I use?" },
  { name: "data_describe_cube", group: "Data", purpose: "Shows a cube's dimensions, measures, joins and descriptions.", prompt: "What's in the orders cube?" },
  { name: "data_query", group: "Data", purpose: "Runs a typed query through the semantic layer and returns rows plus the compiled SQL.", prompt: "Revenue share by region, last 6 months" },
  { name: "data_search_values", group: "Data", purpose: "Finds actual values in a dimension, so filters use real spellings.", prompt: "Which order statuses exist?" },
  { name: "data_propose_migration", group: "Data", purpose: "Proposes a change to the data model. Muse Spark can draft it. A person reviews the diff and applies it here; agents can't apply.", prompt: "Add a late-delivery rate measure" },
];

export function snippets(mcpUrl: string, token?: string) {
  const header = `Authorization: Bearer ${token ?? "$" + TOKEN_ENV}`;
  return {
    env: `export ${TOKEN_ENV}=${token ?? "sc_pat_…"}`,
    claudeCode: `claude mcp add --transport http shipcube ${mcpUrl} \\\n  --scope user \\\n  --header "${header}"`,
    claudeProject: JSON.stringify(
      { mcpServers: { shipcube: { type: "http", url: mcpUrl, headers: { Authorization: "Bearer ${" + TOKEN_ENV + "}" } } } },
      null,
      2,
    ),
    claudeVerify: "claude mcp list",
    cursor: JSON.stringify(
      { mcpServers: { shipcube: { url: mcpUrl, headers: { Authorization: "Bearer ${env:" + TOKEN_ENV + "}" } } } },
      null,
      2,
    ),
    generic: JSON.stringify(
      { mcpServers: { shipcube: { type: "http", url: mcpUrl, headers: { Authorization: "Bearer <your token>" } } } },
      null,
      2,
    ),
    stdio: JSON.stringify(
      {
        mcpServers: {
          shipcube: {
            command: "npx",
            args: ["-y", "mcp-remote", mcpUrl, "--header", "Authorization:${AUTH_HEADER}"],
            env: { AUTH_HEADER: "Bearer <your token>" },
          },
        },
      },
      null,
      2,
    ),
  };
}
