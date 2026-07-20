import assert from "node:assert/strict";
import test from "node:test";

import { NextRequest } from "next/server";

import { proxyApiRequest } from "../lib/api-proxy";

const RESUME_UPLOAD_SEGMENTS = ["me", "resume-versions", "upload"];

test("the proxy reserves a larger request envelope only for resume uploads", async () => {
  const originalFetch = globalThis.fetch;
  const forwarded: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    forwarded.push({ url: String(input), init });
    return Response.json({ imported: true }, { status: 201 });
  }) as typeof fetch;

  const mediumBody = new Uint8Array(600 * 1024);
  try {
    const uploadResponse = await proxyApiRequest(
      new NextRequest("http://localhost/api/me/resume-versions/upload", {
        method: "POST",
        headers: {
          "content-type": "multipart/form-data; boundary=resume-test",
          "idempotency-key": "resume-upload:test",
        },
        body: mediumBody,
      }),
      RESUME_UPLOAD_SEGMENTS,
    );
    assert.equal(uploadResponse.status, 201);
    assert.deepEqual(await uploadResponse.json(), { imported: true });
    assert.equal(forwarded.length, 1);
    assert.equal(
      new Headers(forwarded[0]?.init?.headers).get("idempotency-key"),
      "resume-upload:test",
    );
    assert.equal((forwarded[0]?.init?.body as ArrayBuffer).byteLength, mediumBody.byteLength);

    const ordinaryResponse = await proxyApiRequest(
      new NextRequest("http://localhost/api/me/profile", {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body: mediumBody,
      }),
      ["me", "profile"],
    );
    assert.equal(ordinaryResponse.status, 413);
    assert.equal((await ordinaryResponse.json()).code, "request_too_large");
    assert.equal(forwarded.length, 1, "oversized ordinary requests never reach the backend");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("the proxy rejects a resume multipart envelope above 4 MiB before forwarding", async () => {
  const originalFetch = globalThis.fetch;
  let forwarded = false;
  globalThis.fetch = (async () => {
    forwarded = true;
    return Response.json({ imported: true });
  }) as typeof fetch;

  try {
    const response = await proxyApiRequest(
      new NextRequest("http://localhost/api/me/resume-versions/upload", {
        method: "POST",
        headers: {
          "content-length": String(4 * 1024 * 1024 + 1),
          "content-type": "multipart/form-data; boundary=resume-test",
        },
        body: new Uint8Array([1]),
      }),
      RESUME_UPLOAD_SEGMENTS,
    );
    assert.equal(response.status, 413);
    assert.equal((await response.json()).message, "Resume files must be 3 MB or smaller.");
    assert.equal(forwarded, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
