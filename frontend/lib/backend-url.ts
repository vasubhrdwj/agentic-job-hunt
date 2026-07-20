export class BackendConfigError extends Error {}

export function backendBaseUrl(
  environment: Record<string, string | undefined> = process.env,
): URL {
  const production = environment.NODE_ENV === "production";
  const configured = environment.API_BASE_URL?.trim();
  if (!configured) {
    if (production) {
      throw new BackendConfigError("API_BASE_URL is required in production");
    }
    return new URL("http://127.0.0.1:8000/");
  }

  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    throw new BackendConfigError("API_BASE_URL must be an absolute URL");
  }
  if (production ? url.protocol !== "https:" : !["http:", "https:"].includes(url.protocol)) {
    throw new BackendConfigError(
      production
        ? "API_BASE_URL must use https in production"
        : "API_BASE_URL must use http or https",
    );
  }
  if (url.username || url.password) {
    throw new BackendConfigError("API_BASE_URL must not include credentials");
  }
  url.pathname = url.pathname.replace(/\/+$/, "") + "/";
  url.search = "";
  url.hash = "";
  return url;
}
