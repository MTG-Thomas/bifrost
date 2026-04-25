export const APP_VERSION: string =
  (import.meta.env.VITE_BIFROST_VERSION as string | undefined) ?? "unknown";
