// Errors the console understands. Safe to import from client components.

export type ErrorCode =
  | "unauthenticated"
  | "permission_denied"
  | "not_found"
  | "conflict"
  | "gone"
  | "invalid_argument"
  | "rate_limited"
  | "unavailable"
  | "timeout"
  | "network"
  | "not_connected"
  | "internal";

export class ApiError extends Error {
  readonly name = "ApiError";
  constructor(
    readonly code: ErrorCode | string,
    message: string,
    readonly status: number,
    readonly requestId: string,
  ) {
    super(message);
  }

  /** Worth retrying an idempotent request: the server or the network failed, not the request. */
  get retryable() {
    return ["unavailable", "timeout", "network", "rate_limited"].includes(this.code) || this.status >= 500;
  }

  toJSON(): ActionError {
    return { code: this.code, message: this.message, requestId: this.requestId };
  }
}

/** A console feature whose control-plane endpoint doesn't exist yet. */
export class NotConnectedError extends ApiError {
  constructor(readonly feature: string, readonly endpoint: string) {
    super("not_connected", `${feature} needs ${endpoint}, which the control-plane API doesn't serve yet.`, 501, "");
  }
}

export type ActionError = { code: string; message: string; requestId: string };
export type ActionResult<T = null> = { ok: true; data: T } | { ok: false; error: ActionError };

export function codeForStatus(status: number): ErrorCode {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "permission_denied";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 410) return "gone";
  if (status === 400 || status === 422) return "invalid_argument";
  if (status === 429) return "rate_limited";
  if (status === 501) return "not_connected";
  if (status >= 500) return "unavailable";
  return "internal";
}

/** One sentence a person can act on, for an error shown in the UI. */
export function describeError(e: ActionError): string {
  switch (e.code) {
    case "unauthenticated":
      return "Your session ended. Sign in again and retry.";
    case "permission_denied":
      return "Your role in this workspace doesn't allow this. Ask a workspace owner.";
    case "conflict":
      return `${e.message} Reload the page to see the latest state.`;
    case "rate_limited":
      return "Too many requests. Wait a few seconds and try again.";
    case "timeout":
    case "unavailable":
    case "network":
      return "The control-plane API didn't respond. Nothing was changed; try again.";
    default:
      return e.message;
  }
}
