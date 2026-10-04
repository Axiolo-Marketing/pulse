const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;

/** URL of the admin console, honoring Astro's configured base. */
export function adminBaseHref(): string {
  return BASE_URL.endsWith("/") ? `${BASE_URL}admin/` : `${BASE_URL}/admin/`;
}
