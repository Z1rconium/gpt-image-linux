export type AccessStatus = {
  authenticated: boolean;
  expires_at?: string | null;
  turnstile_enabled?: boolean;
  turnstile_site_key?: string | null;
};


