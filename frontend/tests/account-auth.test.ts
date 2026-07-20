import assert from "node:assert/strict";
import test from "node:test";

import {
  parseOwnerSession,
  parseSessionStatus,
} from "../lib/api-contract";
import {
  claimWorkspaceAccount,
  createAccount,
  signInAccount,
} from "../lib/session";

const ACCOUNT_SESSION = {
  owner_id: "owner-1",
  display_name: "Vasu",
  timezone: "Asia/Kolkata",
  local_date: "2026-07-20",
  expires_at: "2026-08-19T10:00:00Z",
  account_attached: true,
  account_email: "vasu@example.com",
};

test("account sessions retain the safe signed-in identity", () => {
  assert.deepEqual(parseOwnerSession(ACCOUNT_SESSION), ACCOUNT_SESSION);
});

test("legacy sessions are represented without inventing an account email", () => {
  const legacySession = {
    ...ACCOUNT_SESSION,
    account_attached: false,
    account_email: null,
  };
  assert.deepEqual(parseOwnerSession(legacySession), legacySession);
  assert.deepEqual(
    parseOwnerSession({
      owner_id: "legacy-owner",
      display_name: "Legacy user",
      timezone: "UTC",
      local_date: "2026-07-20",
      expires_at: "2026-08-19T10:00:00Z",
    }),
    {
      owner_id: "legacy-owner",
      display_name: "Legacy user",
      timezone: "UTC",
      local_date: "2026-07-20",
      expires_at: "2026-08-19T10:00:00Z",
      account_attached: false,
      account_email: null,
    },
  );
});

test("session parsing rejects inconsistent account state", () => {
  assert.throws(
    () =>
      parseOwnerSession({
        ...ACCOUNT_SESSION,
        account_email: null,
      }),
    /invalid owner session response/,
  );
  assert.throws(
    () =>
      parseOwnerSession({
        ...ACCOUNT_SESSION,
        account_attached: false,
      }),
    /invalid owner session response/,
  );
});

test("session status explicitly controls whether signup is offered", () => {
  assert.deepEqual(
    parseSessionStatus({ state: "ready", signup_enabled: true }),
    { state: "ready", signup_enabled: true },
  );
  assert.deepEqual(
    parseSessionStatus({ state: "ready", signup_enabled: false }),
    { state: "ready", signup_enabled: false },
  );
  assert.deepEqual(
    parseSessionStatus({ state: "ready" }),
    { state: "ready", signup_enabled: false },
  );
  assert.throws(
    () => parseSessionStatus({ state: "ready", signup_enabled: "yes" }),
    /invalid session status response/,
  );
});

test("account actions send email credentials only to their intended endpoints", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    return Response.json(ACCOUNT_SESSION);
  }) as typeof fetch;

  try {
    await signInAccount({ email: "  VASU@example.com ", password: "long-password" });
    await createAccount({
      displayName: " Vasu ",
      email: " vasu@example.com ",
      password: "long-password",
    });
    await claimWorkspaceAccount({
      email: " vasu@example.com ",
      password: "long-password",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls.map((call) => call.url), [
    "/api/session",
    "/api/accounts",
    "/api/accounts/claim",
  ]);
  assert.deepEqual(calls[0]?.body, {
    email: "VASU@example.com",
    password: "long-password",
  });
  assert.equal(calls[1]?.body.display_name, "Vasu");
  assert.equal(calls[1]?.body.email, "vasu@example.com");
  assert.equal(typeof calls[1]?.body.timezone, "string");
  assert.deepEqual(calls[2]?.body, {
    email: "vasu@example.com",
    password: "long-password",
  });
  assert.equal("owner_token" in calls[0]!.body, false);
  assert.equal("owner_token" in calls[1]!.body, false);
  assert.equal("owner_token" in calls[2]!.body, false);
});
