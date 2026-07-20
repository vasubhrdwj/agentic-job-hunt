import type { NextRequest } from "next/server";

import { BackendConfigError, backendBaseUrl } from "./backend-url";

const DEFAULT_MAX_REQUEST_BYTES = 512 * 1024;
const RESUME_UPLOAD_MAX_REQUEST_BYTES = 4 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const PRIVACY_EXPORT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
const DEFAULT_UPSTREAM_TIMEOUT_MS = 20_000;

const MAX_REQUEST_BYTES = integerSetting(
  "API_PROXY_MAX_REQUEST_BYTES",
  DEFAULT_MAX_REQUEST_BYTES,
  16 * 1024,
  4 * 1024 * 1024,
);
const MAX_RESPONSE_BYTES = integerSetting(
  "API_PROXY_MAX_RESPONSE_BYTES",
  DEFAULT_MAX_RESPONSE_BYTES,
  64 * 1024,
  32 * 1024 * 1024,
);
const UPSTREAM_TIMEOUT_MS = integerSetting(
  "API_PROXY_TIMEOUT_MS",
  DEFAULT_UPSTREAM_TIMEOUT_MS,
  1_000,
  120_000,
);

const REQUEST_HEADER_ALLOWLIST = [
  "accept",
  "authorization",
  "content-type",
  "idempotency-key",
  "if-match",
  "origin",
  "x-csrf-token",
  "x-run-access-token",
] as const;

const RESPONSE_HEADER_ALLOWLIST = [
  "cache-control",
  "content-disposition",
  "content-type",
  "deprecation",
  "etag",
  "link",
  "pragma",
  "retry-after",
  "sunset",
  "www-authenticate",
  "x-content-type-options",
  "x-request-id",
] as const;

const SESSION_COOKIE_NAME =
  process.env.JOB_HUNT_SESSION_COOKIE?.trim() || "job_hunt_session";

type BodySource = "request" | "response";

class BodyTooLargeError extends Error {
  constructor(readonly source: BodySource) {
    super(`${source} body exceeds proxy byte limit`);
  }
}

class InvalidContentLengthError extends Error {
  constructor(readonly source: BodySource) {
    super(`${source} has an invalid Content-Length header`);
  }
}

function integerSetting(
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

function upstreamUrl(request: NextRequest, segments: string[]): URL {
  const encodedPath = segments.map((segment) => encodeURIComponent(segment)).join("/");
  const url = new URL(`api/${encodedPath}`, backendBaseUrl());
  url.search = request.nextUrl.search;
  return url;
}

function upstreamHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  for (const name of REQUEST_HEADER_ALLOWLIST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  if (sessionCookie) {
    headers.set("cookie", `${SESSION_COOKIE_NAME}=${sessionCookie}`);
  }
  return headers;
}

function downstreamHeaders(upstream: Response): Headers {
  const headers = new Headers({
    "cache-control": "no-store, max-age=0",
    pragma: "no-cache",
  });
  for (const name of RESPONSE_HEADER_ALLOWLIST) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) headers.set("set-cookie", setCookie);
  return headers;
}

function contentLength(headers: Headers, source: BodySource): number | null {
  const raw = headers.get("content-length");
  if (raw === null) return null;
  if (!/^\d+$/.test(raw)) throw new InvalidContentLengthError(source);
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) throw new InvalidContentLengthError(source);
  return value;
}

async function readBodyWithLimit(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
  source: BodySource,
  signal: AbortSignal,
): Promise<ArrayBuffer> {
  if (!body) return new ArrayBuffer(0);

  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let aborted = signal.aborted;
  const cancelOnAbort = () => {
    aborted = true;
    void reader.cancel("body read aborted").catch(() => undefined);
  };
  signal.addEventListener("abort", cancelOnAbort, { once: true });
  if (signal.aborted) cancelOnAbort();
  try {
    if (aborted) throw new DOMException("Body read aborted", "AbortError");
    while (true) {
      const { done, value } = await reader.read();
      if (aborted) throw new DOMException("Body read aborted", "AbortError");
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel("body exceeds proxy byte limit");
        throw new BodyTooLargeError(source);
      }
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener("abort", cancelOnAbort);
    reader.releaseLock();
  }

  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result.buffer;
}

function jsonProblem(
  status: number,
  code: string,
  message: string,
  retryable = false,
): Response {
  return Response.json(
    { status, code, message, retryable },
    {
      status,
      headers: {
        "cache-control": "no-store, max-age=0",
        "content-type": "application/json",
      },
    },
  );
}

function isPaidHuntRequest(method: string, segments: string[]): boolean {
  return method === "POST" && segments.length === 1 && segments[0] === "hunt";
}

function isPrivacyExport(method: string, segments: string[]): boolean {
  return method === "GET" && segments.length === 2
    && segments[0] === "privacy" && segments[1] === "export";
}

function isResumeUpload(method: string, segments: string[]): boolean {
  return method === "POST" && segments.length === 3
    && segments[0] === "me"
    && segments[1] === "resume-versions"
    && segments[2] === "upload";
}

async function hasValidOwnerSession(
  request: NextRequest,
  signal: AbortSignal,
): Promise<boolean> {
  if (!request.cookies.get(SESSION_COOKIE_NAME)?.value) return false;

  const response = await fetch(new URL("api/session", backendBaseUrl()), {
    method: "GET",
    headers: upstreamHeaders(request),
    cache: "no-store",
    redirect: "manual",
    signal,
  });
  await response.body?.cancel();
  if (response.ok) return true;
  if (response.status === 401 || response.status === 403) return false;
  throw new Error("owner session validation failed");
}

export async function proxyApiRequest(
  request: NextRequest,
  segments: string[],
): Promise<Response> {
  const method = request.method.toUpperCase();
  const privacyExport = isPrivacyExport(method, segments);
  const resumeUpload = isResumeUpload(method, segments);
  const maxRequestBytes = resumeUpload
    ? RESUME_UPLOAD_MAX_REQUEST_BYTES
    : MAX_REQUEST_BYTES;
  const maxResponseBytes = privacyExport
    ? PRIVACY_EXPORT_MAX_RESPONSE_BYTES
    : MAX_RESPONSE_BYTES;
  const hasBody = method !== "GET" && method !== "HEAD";
  const abortController = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    abortController.abort();
  }, UPSTREAM_TIMEOUT_MS);
  const abortForClient = () => abortController.abort();
  request.signal.addEventListener("abort", abortForClient, { once: true });

  try {
    if (
      isPaidHuntRequest(method, segments) &&
      !(await hasValidOwnerSession(request, abortController.signal))
    ) {
      return jsonProblem(
        401,
        "owner_session_required",
        "Sign in to the private workspace before starting a paid hunt.",
      );
    }

    const declaredRequestBytes = hasBody
      ? contentLength(request.headers, "request")
      : null;
    if (declaredRequestBytes !== null && declaredRequestBytes > maxRequestBytes) {
      return jsonProblem(
        413,
        "request_too_large",
        resumeUpload
          ? "Resume files must be 3 MB or smaller."
          : `Request body exceeds the ${maxRequestBytes}-byte limit.`,
      );
    }
    const requestBody = hasBody
      ? await readBodyWithLimit(
          request.body,
          maxRequestBytes,
          "request",
          abortController.signal,
        )
      : undefined;

    const upstream = await fetch(upstreamUrl(request, segments), {
      method,
      headers: upstreamHeaders(request),
      body: requestBody,
      cache: "no-store",
      redirect: "manual",
      signal: abortController.signal,
    });
    const hasResponseBody =
      method !== "HEAD" && ![204, 205, 304].includes(upstream.status);
    const declaredResponseBytes = hasResponseBody
      ? contentLength(upstream.headers, "response")
      : null;
    if (declaredResponseBytes !== null && declaredResponseBytes > maxResponseBytes) {
      await upstream.body?.cancel();
      return jsonProblem(
        privacyExport ? 413 : 502,
        privacyExport ? "privacy_export_too_large" : "upstream_response_too_large",
        privacyExport
          ? "The workspace export exceeds the 32 MiB download limit. Shorten legacy-run retention and retry."
          : "The job-search service returned an unexpectedly large response.",
      );
    }
    const responseBody = hasResponseBody
      ? await readBodyWithLimit(
          upstream.body,
          maxResponseBytes,
          "response",
          abortController.signal,
        )
      : null;
    return new Response(responseBody, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: downstreamHeaders(upstream),
    });
  } catch (error) {
    if (error instanceof BackendConfigError) {
      return jsonProblem(
        503,
        "backend_not_configured",
        "The website backend connection is not configured correctly.",
      );
    }
    if (error instanceof InvalidContentLengthError) {
      return error.source === "request"
        ? jsonProblem(
            400,
            "invalid_content_length",
            "The request has an invalid Content-Length header.",
          )
        : jsonProblem(
            502,
            "invalid_upstream_response",
            "The job-search service returned an invalid response.",
          );
    }
    if (error instanceof BodyTooLargeError) {
      return error.source === "request"
        ? jsonProblem(
            413,
            "request_too_large",
            resumeUpload
              ? "Resume files must be 3 MB or smaller."
              : `Request body exceeds the ${maxRequestBytes}-byte limit.`,
          )
        : jsonProblem(
            privacyExport ? 413 : 502,
            privacyExport ? "privacy_export_too_large" : "upstream_response_too_large",
            privacyExport
              ? "The workspace export exceeds the 32 MiB download limit. Shorten legacy-run retention and retry."
              : "The job-search service returned an unexpectedly large response.",
          );
    }
    if (timedOut) {
      return jsonProblem(
        504,
        "backend_timeout",
        "The job-search service took too long to respond.",
        true,
      );
    }
    return jsonProblem(
      502,
      "backend_unavailable",
      "The job-search service is temporarily unavailable.",
      true,
    );
  } finally {
    clearTimeout(timeout);
    request.signal.removeEventListener("abort", abortForClient);
  }
}
