// The console depends only on this interface. Production uses Identity Platform (security's
// decision, docs/research/security-tenancy.md §1); DEV_MODE can use the mock adapter.

export interface User {
  id: string;
  email: string;
  name: string;
  /** The account's email domain. Used to suggest domain-wide sharing. */
  hostedDomain?: string;
  avatarUrl?: string;
}

export interface Session {
  user: User;
  expiresAt: string;
  /** True once the user has finished (or skipped) onboarding in this browser. */
  onboarded: boolean;
}

export type Intent = "sign-in" | "sign-up";

export interface AuthProvider {
  readonly id: "identity-platform" | "mock";
  getSession(): Promise<Session | null>;
  /**
   * Exchange a fresh Identity Platform ID token (from the browser's Google sign-in) for an
   * httpOnly session cookie. The mock adapter ignores the token.
   */
  createSession(idToken: string, intent: Intent): Promise<{ next: "onboarding" | "console" }>;
  completeOnboarding(): Promise<void>;
  signOut(): Promise<void>;
  /** The credential the console sends to the control-plane API for this user. */
  apiCredential(): Promise<string | null>;
}

export const SESSION_COOKIE = "sc_session";
export const ONBOARDED_COOKIE = "sc_onboarded";

export class AuthError extends Error {
  constructor(
    readonly code: "invalid_token" | "stale_sign_in" | "not_invited" | "email_unverified" | "misconfigured",
    message: string,
  ) {
    super(message);
  }
}
