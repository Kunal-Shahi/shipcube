// MCP connection details shown on the setup guide. The backend networking workstream
// owns the final endpoint, transport and auth; change them here when it lands.

export const MCP_URL = "https://mcp.shipcube.dev/mcp";
export const TOKEN_ENV = "SHIPCUBE_TOKEN";

export const SCOPES = [
  { id: "apps:write", label: "Deploy and change apps", detail: "estimate_load, deploy_app, set_caps, ai_config" },
  { id: "access:write", label: "Manage who can open apps", detail: "grant_access, revoke_access" },
  { id: "data:read", label: "Query connected data", detail: "connect_data, query" },
  { id: "logs:read", label: "Read status, logs and metrics", detail: "get_status, get_logs, get_metrics" },
] as const;

export const TOOLS: { name: string; purpose: string; prompt: string }[] = [
  { name: "estimate_load", purpose: "Profiles your code in a sandbox and returns the workload class, tier, resources, monthly cost and the reasoning.", prompt: "How much would this cost to run?" },
  { name: "deploy_app", purpose: "Builds a container and deploys it to the chosen tier, on ShipCube's GCP project or yours.", prompt: "Deploy this with ShipCube" },
  { name: "set_caps", purpose: "Sets spend, request-rate, concurrency, instance and token limits for an app.", prompt: "Cap it at $20 a month" },
  { name: "grant_access", purpose: "Lets a person, Google group or whole domain open an app.", prompt: "Share it with finance-team@acme.com" },
  { name: "revoke_access", purpose: "Removes someone's access. Takes effect on their next request.", prompt: "Remove alex@acme.com from the tracker" },
  { name: "connect_data", purpose: "Registers a data source and its semantic model (cubes, dimensions, measures).", prompt: "Connect our orders warehouse" },
  { name: "query", purpose: "Runs a typed query through the semantic layer and returns rows plus the compiled SQL.", prompt: "Revenue share by region, last 6 months" },
  { name: "get_status", purpose: "Health, tier, URL and caps for one app or all of them.", prompt: "What's running right now?" },
  { name: "get_logs", purpose: "Build, deploy and request logs, filtered by level or time.", prompt: "Why is the scorecard returning 503s?" },
  { name: "get_metrics", purpose: "Latency, freshness, cost and drift over a time window.", prompt: "How much did my apps cost this week?" },
  { name: "ai_config", purpose: "Chooses the model provider an app uses through the AI gateway: Muse by default, or a BYOK key.", prompt: "Use our Fireworks key for the triage app" },
];

export function snippets(token?: string) {
  const header = `Authorization: Bearer ${token ?? "$" + TOKEN_ENV}`;
  return {
    env: `export ${TOKEN_ENV}=${token ?? "sc_pat_…"}`,
    claudeCode: `claude mcp add --transport http shipcube ${MCP_URL} \\\n  --scope user \\\n  --header "${header}"`,
    claudeVerify: "claude mcp list",
    cursor: JSON.stringify(
      { mcpServers: { shipcube: { url: MCP_URL, headers: { Authorization: `Bearer ${token ?? "${env:" + TOKEN_ENV + "}"}` } } } },
      null,
      2,
    ),
    generic: JSON.stringify(
      { mcpServers: { shipcube: { type: "http", url: MCP_URL, headers: { Authorization: `Bearer ${token ?? "<your token>"}` } } } },
      null,
      2,
    ),
    stdio: JSON.stringify(
      {
        mcpServers: {
          shipcube: {
            command: "npx",
            args: ["-y", "mcp-remote", MCP_URL, "--header", "Authorization:${AUTH_HEADER}"],
            env: { AUTH_HEADER: `Bearer ${token ?? "<your token>"}` },
          },
        },
      },
      null,
      2,
    ),
  };
}
