import { timingSafeEqual } from "node:crypto";

import { BackendConfigError, backendBaseUrl } from "./backend-url";

const MIN_CRON_SECRET_CHARS = 32;
const WAKE_TIMEOUT_MS = 55_000;
const MAX_RESPONSE_BYTES = 16_384;

type Environment = Record<string, string | undefined>;
type Fetcher = typeof fetch;

export async function wakeSleepingBackend(
  request: Request,
  environment: Environment = process.env,
  fetcher: Fetcher = fetch,
): Promise<Response> {
  const secret = environment.CRON_SECRET ?? "";
  if (secret.length < MIN_CRON_SECRET_CHARS) {
    return problem(503, "cadence_not_configured", "The daily cadence is not configured.");
  }
  const authorization = request.headers.get("authorization") ?? "";
  if (!safeEqual(authorization, `Bearer ${secret}`)) {
    return problem(401, "cadence_authorization_required", "Cadence authorization is required.");
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), WAKE_TIMEOUT_MS);
  try {
    const url = new URL("internal/cadence/tick", backendBaseUrl(environment));
    const upstream = await fetcher(url, {
      method: "POST",
      headers: { Authorization: authorization },
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
    const body = await readBoundedBody(upstream, MAX_RESPONSE_BYTES);
    if (!upstream.ok) {
      return problem(
        502,
        "cadence_backend_rejected",
        "The job-search cadence wake was not accepted by the backend.",
      );
    }
    return new Response(body, {
      status: 200,
      headers: {
        "Cache-Control": "no-store, max-age=0",
        "Content-Type": "application/json",
      },
    });
  } catch (error) {
    if (error instanceof BackendConfigError) {
      return problem(503, "backend_not_configured", "The backend connection is not configured.");
    }
    return problem(
      error instanceof Error && error.name === "AbortError" ? 504 : 502,
      "cadence_backend_unavailable",
      "The job-search backend could not be woken for its daily cadence.",
    );
  } finally {
    clearTimeout(timeout);
  }
}

function safeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

async function readBoundedBody(response: Response, limit: number): Promise<string> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > limit) throw new Error("cadence response exceeded limit");
  return new TextDecoder().decode(bytes);
}

function problem(status: number, code: string, message: string): Response {
  return Response.json(
    { status, code, message, retryable: status >= 500 },
    {
      status,
      headers: { "Cache-Control": "no-store, max-age=0" },
    },
  );
}
