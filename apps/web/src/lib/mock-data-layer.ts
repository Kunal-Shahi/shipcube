// Mock semantic-layer catalog for the Olist demo. Op names, migration kinds and the
// dry-run impact shape follow docs/MIGRATIONS.md in Kunal-Shahi/shipcube-data.

import { MOCK_NOW } from "./mock-data";
import type { Cube, Migration, Warehouse } from "./types";

const ago = (h: number) => new Date(MOCK_NOW - h * 3600_000).toISOString();

export const warehouse: Warehouse = {
  connected: true,
  kind: "bigquery",
  name: "Olist warehouse",
  details: [
    { label: "Project", value: "acme-analytics-prod" },
    { label: "Datasets", value: "tenant_acme, shared" },
    { label: "Location", value: "US" },
    { label: "Service account", value: "shipcube-reader@acme-analytics-prod.iam.gserviceaccount.com" },
    { label: "Access", value: "BigQuery Data Viewer, BigQuery Job User (read-only)" },
    { label: "Query byte cap", value: "10 GB per query" },
  ],
  connectedAt: "2026-10-14T09:30:00Z",
  connectedBy: "kunal@acme-analytics.com",
  lastCheckedAt: ago(0.2),
  modelVersion: 5,
};

export const cubes: Cube[] = [
  {
    name: "orders",
    title: "Orders",
    description: "One row per order placed on Olist, with its status and delivery timestamps.",
    table: "orders",
    schema: "tenant_{tenant}",
    dimensions: [
      { name: "order_id", type: "string", sql: "order_id", description: "Primary key" },
      { name: "status", type: "string", sql: "order_status", description: "delivered, shipped, canceled, …" },
      { name: "purchased_at", type: "time", sql: "order_purchase_timestamp" },
      { name: "customer_id", type: "string", sql: "customer_id" },
    ],
    measures: [
      { name: "count", type: "count", sql: "COUNT(*)" },
      { name: "delivered_count", type: "count", sql: "COUNT(CASE WHEN order_status = 'delivered' THEN 1 END)" },
      { name: "unique_customers", type: "count_distinct", sql: "COUNT(DISTINCT customer_id)" },
    ],
    joins: [{ to: "customers", relationship: "many_to_one", on: "orders.customer_id = customers.customer_id" }],
    segments: [{ name: "delivered", sql: "order_status = 'delivered'" }],
    partition: "orders_{yyyy}_{mm} tables by purchased_at, 2017-01 to 2018-08",
    policies: [],
    usedBy: ["regional-revenue-tracker", "seller-scorecard", "weekly-ops-report"],
  },
  {
    name: "order_items",
    title: "Order items",
    description: "One row per item in an order, with price and freight.",
    table: "order_items",
    schema: "tenant_{tenant}",
    dimensions: [
      { name: "order_id", type: "string", sql: "order_id" },
      { name: "product_id", type: "string", sql: "product_id" },
      { name: "seller_id", type: "string", sql: "seller_id" },
      { name: "shipping_limit_at", type: "time", sql: "shipping_limit_date" },
    ],
    measures: [
      { name: "revenue", type: "sum", sql: "SUM(price)", description: "Gross item revenue in BRL, before freight" },
      { name: "freight", type: "sum", sql: "SUM(freight_value)", restrictedTo: ["finance"] },
      { name: "items", type: "count", sql: "COUNT(*)" },
    ],
    joins: [
      { to: "orders", relationship: "many_to_one", on: "order_items.order_id = orders.order_id" },
      { to: "products", relationship: "many_to_one", on: "order_items.product_id = products.product_id" },
      { to: "sellers", relationship: "many_to_one", on: "order_items.seller_id = sellers.seller_id" },
    ],
    segments: [],
    partition: "column shipping_limit_date, monthly",
    policies: [],
    usedBy: ["regional-revenue-tracker", "seller-scorecard", "weekly-ops-report"],
  },
  {
    name: "customers",
    title: "Customers",
    description: "Customers and where they live.",
    table: "customers",
    schema: "tenant_{tenant}",
    dimensions: [
      { name: "customer_id", type: "string", sql: "customer_id" },
      { name: "city", type: "string", sql: "customer_city" },
      { name: "state", type: "string", sql: "customer_state" },
    ],
    measures: [{ name: "count", type: "count", sql: "COUNT(*)" }],
    joins: [{ to: "states", relationship: "many_to_one", on: "customers.customer_state = states.state_code" }],
    segments: [],
    policies: [],
    usedBy: ["regional-revenue-tracker", "churn-scoring-job"],
  },
  {
    name: "sellers",
    title: "Sellers",
    description: "Marketplace sellers and their location.",
    table: "sellers",
    schema: "tenant_{tenant}",
    dimensions: [
      { name: "seller_id", type: "string", sql: "seller_id" },
      { name: "state", type: "string", sql: "seller_state" },
    ],
    measures: [{ name: "count", type: "count", sql: "COUNT(*)" }],
    joins: [],
    segments: [],
    policies: [],
    usedBy: ["seller-scorecard"],
  },
  {
    name: "products",
    title: "Products",
    description: "Product catalog with category names.",
    table: "products",
    schema: "tenant_{tenant}",
    dimensions: [
      { name: "product_id", type: "string", sql: "product_id" },
      { name: "category", type: "string", sql: "product_category_name" },
      { name: "weight_g", type: "number", sql: "product_weight_g" },
    ],
    measures: [{ name: "count", type: "count", sql: "COUNT(*)" }],
    joins: [],
    segments: [],
    policies: [],
    usedBy: ["seller-scorecard"],
  },
  {
    name: "states",
    title: "Brazilian states",
    description: "Reference table: state codes, names and regions.",
    table: "states",
    schema: "shared",
    dimensions: [
      { name: "state_code", type: "string", sql: "state_code" },
      { name: "name", type: "string", sql: "state_name" },
      { name: "region", type: "string", sql: "region", description: "Norte, Nordeste, Centro-Oeste, Sudeste, Sul" },
    ],
    measures: [],
    joins: [],
    segments: [],
    policies: ["orders.own_region: regional managers see only their region"],
    usedBy: ["regional-revenue-tracker"],
  },
];

const claude = { agent: "Claude Code", tokenName: "MacBook – Claude Code", user: "kunal@acme-analytics.com" };
const human = { agent: "CLI", tokenName: "shipcube-data migrate", user: "kunal@acme-analytics.com" };

export const migrations: Migration[] = [
  {
    id: "0006_late_delivery_rate",
    description: "Late-delivery rate per order, for the seller scorecard. An order is late when it arrives after the estimated date.",
    status: "proposed",
    kind: "additive",
    proposedBy: claude,
    draftedBy: "Muse Spark",
    proposedAt: ago(0.6),
    baseVersion: 5,
    ops: [
      { op: "add_dimension", name: "orders.delivered_at", sql: "order_delivered_customer_date" },
      { op: "add_dimension", name: "orders.estimated_delivery_at", sql: "order_estimated_delivery_date" },
      { op: "add_dimension", name: "orders.is_late", sql: "{delivered_at} > {estimated_delivery_at}" },
      { op: "add_measure", name: "orders.late_count", agg: "count", filter: "{is_late}" },
      { op: "add_measure", name: "orders.late_delivery_rate", sql: "{late_count} / nullif({delivered_count}, 0)", format: "percent" },
    ],
    diff: {
      added: ["members:orders.delivered_at", "members:orders.estimated_delivery_at", "members:orders.is_late", "members:orders.late_count", "members:orders.late_delivery_rate"],
      changed: [],
      removed: [],
    },
    impact: { breaks: [], changes: [], warnings: [], unaffected: 14, appsBroken: [] },
  },
  {
    id: "0007_net_revenue",
    description: "Revenue is net of freight from now on, to match finance's definition.",
    status: "proposed",
    kind: "change",
    proposedBy: claude,
    proposedAt: ago(2.1),
    baseVersion: 5,
    ops: [{ op: "change_sql", member: "order_items.revenue", sql: "price - freight_value" }],
    diff: { added: [], changed: ["members:order_items.revenue"], removed: [] },
    impact: {
      breaks: [],
      changes: [
        { query: "revenue_by_state", app: "regional-revenue-tracker", note: "Compiled SQL changes, so revenue figures drop by the freight share (about 14%)." },
        { query: "revenue_by_seller", app: "seller-scorecard", note: "Compiled SQL changes." },
        { query: "weekly_freight_summary", app: "weekly-ops-report", note: "Compiled SQL changes." },
      ],
      warnings: [],
      unaffected: 11,
      appsBroken: [],
    },
  },
  {
    id: "0008_rework_items",
    description: "Rename purchased_at to ordered_at and drop the products join, which nothing should need now that category lives on items.",
    status: "proposed",
    kind: "breaking",
    proposedBy: { agent: "Cursor", tokenName: "Cursor", user: "kunal@acme-analytics.com" },
    proposedAt: ago(6.4),
    baseVersion: 5,
    ops: [
      { op: "rename_member", member: "orders.purchased_at", to: "orders.ordered_at" },
      { op: "drop_join", cube: "order_items", to: "products" },
    ],
    diff: {
      added: ["member_aliases:orders.purchased_at", "members:orders.ordered_at"],
      changed: [],
      removed: ["joins:order_items.products", "members:orders.purchased_at"],
    },
    impact: {
      breaks: [{ query: "revenue_by_category", app: "seller-scorecard", error: "no join path from order_items to products" }],
      changes: [],
      warnings: [
        { query: "orders_by_month", app: "weekly-ops-report", warnings: ["orders.purchased_at was renamed to orders.ordered_at; update the query"] },
        { query: "monthly_share", app: "regional-revenue-tracker", warnings: ["orders.purchased_at was renamed to orders.ordered_at; update the query"] },
      ],
      unaffected: 11,
      appsBroken: ["seller-scorecard"],
    },
  },
  ...[
    ["0005_access", "Freight is finance-only; regional managers see their own region", "breaking", "2026-10-16T10:00:00Z"],
    ["0004_partitions", "Monthly order tables and the item partition column", "change", "2026-10-15T09:00:00Z"],
    ["0003_joins", "Join orders, items, customers, sellers, products and states", "additive", "2026-10-14T11:00:00Z"],
    ["0002_base_cubes", "Olist base cubes", "change", "2026-10-14T10:30:00Z"],
    ["0001_bind_schemas", "Every cube reads from its tenant's schema; reference data lives in shared", "change", "2026-10-14T10:00:00Z"],
  ].map(([id, description, kind, at], i): Migration => ({
    id,
    description,
    status: "applied",
    kind: kind as Migration["kind"],
    proposedBy: human,
    proposedAt: at,
    baseVersion: 4 - i,
    ops: [],
    diff: { added: [], changed: [], removed: [] },
    impact: { breaks: [], changes: [], warnings: [], unaffected: 0, appsBroken: [] },
    applied: { version: 5 - i, at, by: "kunal@acme-analytics.com" },
  })),
];
