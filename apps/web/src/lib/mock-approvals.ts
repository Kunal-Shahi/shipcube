// Mock approval requests. Shapes follow the decided approval flow: the agent's tool call
// returns status "awaiting_approval" with an approval_url that opens /approvals/<id>.

import { MOCK_NOW } from "./mock-data";
import type { Approval } from "./types";

const ago = (h: number) => new Date(MOCK_NOW - h * 3600_000).toISOString();
const ahead = (h: number) => new Date(MOCK_NOW + h * 3600_000).toISOString();

const claude = { agent: "Claude Code", tokenName: "MacBook – Claude Code", user: "kunal@acme-analytics.com" };
const cursor = { agent: "Cursor", tokenName: "Cursor", user: "kunal@acme-analytics.com" };

export const WORKSPACE_DEFAULT_SPEND_CAP = 50;

export const approvals: Approval[] = [
  {
    id: "apr_7k2m",
    kind: "linked_gcp_deploy",
    status: "pending",
    title: "Deploy freight-cost-explorer to your GCP project",
    appName: "freight-cost-explorer",
    requestedBy: claude,
    requestedAt: ago(0.1),
    expiresAt: ahead(23.9),
    prompt: "Deploy this freight explorer to our GCP project and share it with ops-leads@",
    reason:
      "This is the first deploy of freight-cost-explorer into acme-analytics-prod. Its resources are created in your project and Google bills your account, so a person approves it once. Later deploys of this app go ahead without asking.",
    effects: [
      "ShipCube builds the container with Cloud Build in acme-analytics-prod.",
      "It creates the Cloud Run service freight-cost-explorer in us-central1, scaled to zero when idle.",
      "The app is private. Only you can open it until access is granted.",
      "The agent's deploy continues automatically and reports the URL.",
    ],
    impact: [
      { label: "Billed to", after: "acme-analytics-prod (billing account 01A2B3-C4D5E6-F7G8H9)" },
      { label: "Tier", after: "T1 Serverless, 1 vCPU / 1 GiB, 0–4 instances" },
      { label: "Estimated cost", after: "$11.40 / month" },
      { label: "Spend cap", after: "$25.00 / month (workspace default)" },
      { label: "Build minutes", after: "About 3 per deploy, from your project's free 2,500" },
    ],
    museNote:
      "Freight lookups run 4 warehouse queries per page and the result cache covers most repeat views. Traffic should follow ops working hours, so scale-to-zero fits.",
    tool: { name: "deploy_app", args: { name: "freight-cost-explorer", tier: "T1", project: "acme-analytics-prod", region: "us-central1", idempotency_key: "dep-8c1e" } },
  },
  {
    id: "apr_c4q9",
    kind: "cap_raise",
    status: "pending",
    title: "Raise the monthly spend cap on seller-scorecard",
    appId: "app_ssc02",
    appName: "seller-scorecard",
    requestedBy: cursor,
    requestedAt: ago(1.3),
    expiresAt: ahead(22.7),
    prompt: "Black Friday is coming; make sure the scorecard doesn't hit its cap",
    reason: `The new cap is above the workspace default of $${WORKSPACE_DEFAULT_SPEND_CAP} per month. Lowering a cap never needs approval; raising one past the default does.`,
    effects: [
      "The spend cap changes immediately.",
      "Request-rate and concurrency caps stay as they are.",
      "ShipCube still emails you at 80% of the new cap.",
    ],
    impact: [
      { label: "Monthly spend cap", before: "$40.00", after: "$120.00", tone: "warning" },
      { label: "Spent this month", after: "$24.23" },
      { label: "Forecast for October", after: "$31.40" },
      { label: "Workspace default", after: `$${WORKSPACE_DEFAULT_SPEND_CAP}.00` },
    ],
    museNote:
      "Last year's traces show traffic roughly doubling in the week of Black Friday. At the current tier that is about $58 for November. A cap of $70 covers it with headroom; $120 is more than the forecast needs. Moving to T3 (see the app's suggestion) would lower the forecast to about $41.",
    tool: { name: "set_caps", args: { app: "seller-scorecard", spend_usd_per_month: 120 } },
  },
  {
    id: "apr_d8w1",
    kind: "domain_grant",
    status: "pending",
    title: "Share regional-revenue-tracker with everyone at acme-analytics.com",
    appId: "app_rrt01",
    appName: "regional-revenue-tracker",
    requestedBy: claude,
    requestedAt: ago(3.5),
    expiresAt: ahead(20.5),
    prompt: "Let the whole company see the revenue tracker",
    reason:
      "A domain grant lets anyone who signs in with a Google account on that domain open the app, including people who join later. Adding named people or groups doesn't need approval.",
    effects: [
      "Anyone signed in as …@acme-analytics.com can open the app as a viewer.",
      "Existing grants stay as they are.",
      "You can remove the domain later from the app's Access tab.",
    ],
    impact: [
      { label: "Who can open it", before: "4 grants (you, 2 people or groups, 1 partner domain)", after: "Everyone at acme-analytics.com", tone: "warning" },
      { label: "Data shown", after: "Revenue and order share by Brazilian state" },
      { label: "Row-level rules", after: "None on these cubes, so everyone sees every state" },
    ],
    tool: { name: "set_access", args: { app: "regional-revenue-tracker", add: ["domain:acme-analytics.com"], role: "viewer" } },
  },
  {
    id: "apr_e2n5",
    kind: "delete_app",
    status: "pending",
    title: "Delete weekly-ops-report",
    appId: "app_wor03",
    appName: "weekly-ops-report",
    requestedBy: claude,
    requestedAt: ago(5.2),
    expiresAt: ahead(18.8),
    prompt: "We moved the ops report into the new dashboard, delete the old one",
    reason: "Deleting an app always needs approval. ShipCube keeps a deleted app for 7 days, so you can restore it from the Apps page until then.",
    effects: [
      "The URL stops working right away.",
      "The 8 people and groups who could open it lose access.",
      "The Monday 06:00 BRT refresh stops.",
      "After 7 days ShipCube removes the service and its image. Warehouse data is never touched.",
    ],
    impact: [
      { label: "Status", before: "Healthy, 96 requests in the last 24 h", after: "Deleted, restorable until Nov 3", tone: "critical" },
      { label: "Shared with", before: "8", after: "Nobody" },
      { label: "Monthly cost", before: "about $0.40", after: "$0.00" },
    ],
    tool: { name: "delete_app", args: { app: "weekly-ops-report" } },
  },
  {
    id: "apr_a1b2",
    kind: "linked_gcp_deploy",
    status: "approved",
    title: "Deploy churn-scoring-job to your GCP project",
    appId: "app_csj04",
    appName: "churn-scoring-job",
    requestedBy: claude,
    requestedAt: "2026-10-18T18:58:00Z",
    expiresAt: "2026-10-19T18:58:00Z",
    reason: "First deploy of churn-scoring-job into acme-analytics-prod.",
    effects: ["Created the Cloud Run job churn-scoring-job and a Cloud Scheduler trigger at 03:00 BRT."],
    impact: [{ label: "Estimated cost", after: "$9.10 / month" }],
    tool: { name: "deploy_app", args: { name: "churn-scoring-job", tier: "T2", project: "acme-analytics-prod" } },
    decision: { by: "kunal@acme-analytics.com", at: "2026-10-18T19:00:00Z" },
  },
  {
    id: "apr_f6t3",
    kind: "cap_raise",
    status: "rejected",
    title: "Raise the daily LLM token cap on support-ticket-triage",
    appId: "app_stt06",
    appName: "support-ticket-triage",
    requestedBy: { agent: "Muse Code", tokenName: "Muse Code", user: "kunal@acme-analytics.com" },
    requestedAt: ago(4.8),
    expiresAt: ahead(19.2),
    reason: "The new cap is above the workspace default of 200,000 tokens per day.",
    effects: ["The token cap would change immediately."],
    impact: [{ label: "LLM tokens per day", before: "150,000", after: "1,000,000", tone: "warning" }],
    tool: { name: "set_caps", args: { app: "support-ticket-triage", llm_tokens_per_day: 1_000_000 } },
    decision: { by: "kunal@acme-analytics.com", at: ago(4.5), note: "The classifier retries on every 429. Fix the retry loop first; 150k a day is plenty." },
  },
  {
    id: "apr_k9z0",
    kind: "domain_grant",
    status: "expired",
    title: "Share seller-scorecard with everyone at olist-partners.com.br",
    appId: "app_ssc02",
    appName: "seller-scorecard",
    requestedBy: cursor,
    requestedAt: "2026-10-20T15:00:00Z",
    expiresAt: "2026-10-21T15:00:00Z",
    reason: "Domain grants need approval.",
    effects: ["Anyone at olist-partners.com.br would have been able to open the app."],
    impact: [{ label: "Who can open it", after: "Everyone at olist-partners.com.br", tone: "warning" }],
    tool: { name: "set_access", args: { app: "seller-scorecard", add: ["domain:olist-partners.com.br"] } },
  },
];
