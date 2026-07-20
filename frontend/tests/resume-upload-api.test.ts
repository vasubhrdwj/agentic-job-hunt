import assert from "node:assert/strict";
import test from "node:test";

import { uploadResumeVersion } from "../lib/workspace-api";
import type { ResumeImportReport } from "../lib/workspace-types";

const REPORT: ResumeImportReport = {
  resume_version: {
    id: "resume-1",
    label: "Vasu Backend Resume",
    source: "uploaded",
    parent_resume_version_id: null,
    is_base: true,
    character_count: 120,
    version: 1,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:00Z",
  },
  imported_profile_fields: ["current_title"],
  achievement_suggestions_created: 1,
  missing_profile_fields: ["current_location"],
  warnings: [],
  parsed_sections: ["experience"],
};

test("resume upload sends multipart data and retry identity without inventing a label", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({ url: String(input), init });
    return Response.json(REPORT, { status: 201 });
  }) as typeof fetch;

  const file = new File(["resume text"], "Vasu_Backend_Resume.pdf", {
    type: "application/pdf",
  });
  try {
    assert.deepEqual(
      await uploadResumeVersion({
        file,
        setAsBase: true,
        idempotencyKey: "resume-upload:stable-retry-key",
      }),
      REPORT,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, "/api/me/resume-versions/upload");
  assert.equal(calls[0]?.init?.method, "POST");
  const headers = new Headers(calls[0]?.init?.headers);
  assert.equal(headers.get("idempotency-key"), "resume-upload:stable-retry-key");
  assert.equal(headers.has("content-type"), false, "the browser must add the multipart boundary");
  assert.ok(calls[0]?.init?.body instanceof FormData);
  const body = calls[0].init.body;
  assert.equal(body.get("file"), file);
  assert.equal(body.get("label"), null, "the backend derives the default from the filename");
  assert.equal(body.get("set_as_base"), "true");
});

test("resume upload includes a label only when the owner customizes it", async () => {
  const originalFetch = globalThis.fetch;
  const sentBodies: FormData[] = [];
  globalThis.fetch = (async (_input, init) => {
    if (init?.body instanceof FormData) sentBodies.push(init.body);
    return Response.json(REPORT, { status: 201 });
  }) as typeof fetch;

  try {
    await uploadResumeVersion({
      file: new File(["resume text"], "resume.txt", { type: "text/plain" }),
      label: "Backend · July 2026",
      setAsBase: false,
      idempotencyKey: "resume-upload:custom-label",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(sentBodies.length, 1);
  const sentBody = sentBodies[0];
  assert.ok(sentBody);
  assert.equal(sentBody.get("label"), "Backend · July 2026");
  assert.equal(sentBody.get("set_as_base"), "false");
});
