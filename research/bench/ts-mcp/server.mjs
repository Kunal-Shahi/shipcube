// Minimal MCP server on the official TypeScript SDK, streamable HTTP (stateless), one tool.
import http from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

function build() {
  const server = new McpServer({ name: "shipcube-bench", version: "0.0.1" });
  server.tool("estimate_load", "Estimate workload class", { repo: z.string() }, async () => ({
    content: [{ type: "text", text: JSON.stringify({ class: "bursty_interactive", tier: "T1" }) }],
  }));
  return server;
}

http.createServer(async (req, res) => {
  if (req.url === "/healthz") return res.end("ok");
  if (req.url !== "/mcp") { res.statusCode = 404; return res.end(); }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined;
  const server = build();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  res.on("close", () => { transport.close(); server.close(); });
  await server.connect(transport);
  await transport.handleRequest(req, res, body);
}).listen(Number(process.env.PORT || 8080));
