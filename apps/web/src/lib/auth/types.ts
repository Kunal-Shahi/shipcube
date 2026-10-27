// The console depends only on this interface. The security workstream is choosing
// between Auth.js, Firebase Auth and Identity Platform; each becomes one adapter.

export interface User {
  id: string;
  email: string;
  name: string;
  /** Google Workspace domain, if the account belongs to one. Used for domain-wide sharing. */
  hostedDomain?: string;
  avatarUrl?: string;
}

export interface Session {
  user: User;
  expiresAt: string;
  /** True once the user has finished (or skipped) onboarding. */
  onboarded: boolean;
}

export interface AuthProvider {
  readonly id: string;
  getSession(): Promise<Session | null>;
  /**
   * Start Google sign-in. Real adapters return the provider's redirect URL;
   * the mock signs in immediately and returns where to go next.
   */
  signInWithGoogle(opts: { intent: "sign-in" | "sign-up"; returnTo?: string }): Promise<{ redirectTo: string }>;
  completeOnboarding(): Promise<void>;
  signOut(): Promise<void>;
}

export const SESSION_COOKIE = "sc_session";
