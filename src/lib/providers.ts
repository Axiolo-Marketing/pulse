import { useEffect, useState } from "react";

import { API_BASE } from "./api";

export interface OAuthProviders {
  google: boolean;
  microsoft: boolean;
}

const NONE: OAuthProviders = { google: false, microsoft: false };
// When the check itself fails, keep Google (the provider operators actually
// use) so a transient error can't strand a Google-only account on the
// password form; Microsoft stays hidden unless confirmed configured.
const UNKNOWN: OAuthProviders = { google: true, microsoft: false };

/** Which OAuth providers the backend has configured. Hidden until known;
 * if the check fails, falls back to Google only. */
export function useOAuthProviders(): OAuthProviders {
  const [providers, setProviders] = useState<OAuthProviders>(NONE);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/auth/providers`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error(`providers ${res.status}`);
        const data = (await res.json()) as Partial<OAuthProviders>;
        if (!cancelled) {
          setProviders({ google: !!data.google, microsoft: !!data.microsoft });
        }
      } catch {
        if (!cancelled) setProviders(UNKNOWN);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return providers;
}
