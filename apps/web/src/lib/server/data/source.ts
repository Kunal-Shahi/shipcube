import "server-only";

import type {
  AccessGrant,
  AccountStatus,
  ApiToken,
  AppDetail,
  AppSummary,
  Approval,
  BillingOverview,
  Caps,
  Cube,
  GcpLink,
  Migration,
  ProviderConfig,
  Role,
  Warehouse,
  WaitlistPage,
  WaitlistRequest,
  WaitlistState,
  WarehouseKind,
} from "@/lib/types";

export type WarehouseInput =
  | { kind: "bigquery"; project: string; location: string; serviceAccountKey: string }
  | { kind: Exclude<WarehouseKind, "bigquery">; host: string; port: number; database: string; user: string; password: string; ssl: string };

/** DEV_MODE only: ?state=empty / ?state=error on a page URL. Always undefined against the real API. */
export type DemoState = "empty" | "error" | undefined;

/** Parts of the console whose control-plane endpoints don't exist yet. */
export type Feature = "approvals_inbox" | "tokens" | "providers" | "billing" | "gcp_link" | "data" | "deleted_apps" | "waitlist";

export const FEATURE_ENDPOINT: Record<Feature, { name: string; endpoint: string }> = {
  approvals_inbox: { name: "The approvals inbox", endpoint: "GET /v1/operations?status=awaiting_approval" },
  tokens: { name: "API tokens", endpoint: "GET, POST and DELETE /v1/tokens" },
  providers: { name: "AI providers", endpoint: "GET /v1/ai/providers and PUT /v1/ai/providers/{id}" },
  billing: { name: "Billing", endpoint: "GET /v1/billing" },
  gcp_link: { name: "GCP project linking", endpoint: "GET and POST /v1/gcp-targets" },
  data: { name: "The data model", endpoint: "the data-layer endpoints (/v1/data/…)" },
  deleted_apps: { name: "Recently deleted apps", endpoint: "GET /v1/apps?include_deleted=true" },
  waitlist: { name: "Early-access requests", endpoint: "POST /v1/waitlist, GET /v1/waitlist/me and /v1/admin/waitlist" },
};

/** An action that the API parked for a person to approve. */
export interface PendingApproval {
  id: string;
  url: string;
  reasons: string[];
}

export interface CapsChangeResult {
  caps: Caps;
  approval?: PendingApproval;
}

export interface AccessChangeResult {
  access: AccessGrant[];
  approval?: PendingApproval;
}

export interface ConsoleData {
  readonly kind: "mock" | "http";
  supports(feature: Feature): boolean;

  listApps(state?: DemoState): Promise<AppSummary[]>;
  getApp(id: string, state?: DemoState): Promise<AppDetail | null>;
  saveCaps(appId: string, caps: Partial<Caps>, idempotencyKey: string): Promise<CapsChangeResult>;
  changeAccess(
    appId: string,
    change: { add?: { principal: string; role: Exclude<Role, "owner"> }[]; remove?: string[] },
    idempotencyKey: string,
  ): Promise<AccessChangeResult>;
  restoreApp(appId: string): Promise<void>;

  listApprovals(state?: DemoState): Promise<Approval[]>;
  getApproval(id: string): Promise<Approval | null>;
  decideApproval(id: string, decision: "approve" | "reject", comment?: string): Promise<Approval | null>;
  pendingApprovalCount(): Promise<number | null>;

  listTokens(state?: DemoState): Promise<ApiToken[]>;
  createToken(input: { name: string; scopes: string[]; days: number }): Promise<{ token: ApiToken; secret: string }>;
  revokeToken(id: string): Promise<void>;

  listProviders(): Promise<ProviderConfig[]>;
  /** The key goes straight to Secret Manager through the API; the console never stores it. */
  saveProviderKey(id: ProviderConfig["id"], input: { key: string; baseUrl?: string; model?: string }): Promise<ProviderConfig>;
  removeProviderKey(id: ProviderConfig["id"]): Promise<void>;

  getBilling(state?: DemoState): Promise<BillingOverview>;
  /** The workspace's deployer SA (what link-project.sh grants roles to) and its active linked project, if any. */
  getGcpLink(): Promise<{ deployer: string; link: GcpLink }>;
  /** The API checks the deployer's permissions on the project (testIamPermissions). */
  linkGcpProject(input: { projectId: string; region: string }): Promise<GcpLink>;

  getWarehouse(state?: DemoState): Promise<Warehouse>;
  /** Tests the credentials, then stores them in Secret Manager. Never echoes them back. */
  connectWarehouse(input: WarehouseInput, save: boolean): Promise<{ tables: number; schemas: number; warehouse?: Warehouse }>;
  listCubes(state?: DemoState): Promise<Cube[]>;
  getCube(name: string): Promise<Cube | null>;
  listMigrations(state?: DemoState): Promise<Migration[]>;
  getMigration(id: string): Promise<Migration | null>;
  decideMigration(id: string, decision: "apply" | "reject", opts: { allowBreaking: boolean; note?: string }): Promise<Migration>;

  // Early access. Requests come from the landing page's form, which posts from the browser
  // straight to the API (POST /v1/waitlist); the console reads status and decides.
  /** Signed in without a workspace: request access for the session's own (verified) email. */
  requestAccessSignedIn(input: { name: string; useCase: string }): Promise<void>;
  /** Member, or where the signed-in person's access request stands. Throws only when the API fails. */
  accountStatus(): Promise<AccountStatus>;
  listWaitlist(status: WaitlistState | "all"): Promise<WaitlistPage>;
  decideWaitlist(id: string, decision: "approve" | "reject"): Promise<WaitlistRequest>;
}
