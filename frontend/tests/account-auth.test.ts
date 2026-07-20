import assert from "node:assert/strict";
import test from "node:test";

import {
  parseOwnerSession,
  parseSessionStatus,
} from "../lib/api-contract";
import {
  claimWorkspaceAccount,
  createAccount,
  recoverLegacyWorkspace,
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

test("session status explicitly controls signup and legacy recovery", () => {
  assert.deepEqual(
    parseSessionStatus({
      state: "ready",
      signup_enabled: true,
      legacy_recovery_enabled: true,
    }),
    {
      state: "ready",
      signup_enabled: true,
      legacy_recovery_enabled: true,
    },
  );
  assert.deepEqual(
    parseSessionStatus({
      state: "ready",
      signup_enabled: false,
      legacy_recovery_enabled: false,
    }),
    {
      state: "ready",
      signup_enabled: false,
      legacy_recovery_enabled: false,
    },
  );
  assert.deepEqual(
    parseSessionStatus({ state: "ready" }),
    {
      state: "ready",
      signup_enabled: false,
      legacy_recovery_enabled: false,
    },
  );
  assert.throws(
    () => parseSessionStatus({ state: "ready", signup_enabled: "yes" }),
    /invalid session status response/,
  );
  assert.throws(
    () => parseSessionStatus({ state: "ready", legacy_recovery_enabled: "yes" }),
    /invalid session status response/,
  );
});

test("legacy recovery sends the complete access key and new credentials", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({ url: String(input), init });
    return Response.json(ACCOUNT_SESSION);
  }) as typeof fetch;

  try {
    const recovered = await recoverLegacyWorkspace({
      recoveryToken: "  previous-private-access-key  ",
      email: "  VASU@example.com  ",
      password: "new-password-kept-exact",
    });
    assert.deepEqual(recovered, ACCOUNT_SESSION);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, "/api/accounts/recover");
  assert.equal(calls[0]?.init?.method, "POST");
  assert.equal(calls[0]?.init?.credentials, "same-origin");
  assert.equal(
    new Headers(calls[0]?.init?.headers).get("content-type"),
    "application/json",
  );
  assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
    recovery_token: "previous-private-access-key",
    email: "VASU@example.com",
    password: "new-password-kept-exact",
  });
});

test("legacy recovery maps safe errors for every expected failure class", async () => {
  const originalFetch = globalThis.fetch;
  let responseStatus = 401;
  globalThis.fetch = (async () =>
    Response.json({ detail: "private upstream detail" }, { status: responseStatus })) as typeof fetch;

  const cases: Array<[number, RegExp]> = [
    [401, /previous access key is incorrect/i],
    [409, /recovery may already be complete.*try sign in/i],
    [422, /check the email, new password, and complete previous access key/i],
    [429, /recovery is temporarily limited/i],
    [502, /job-search service is temporarily unavailable/i],
    [503, /job-search service is temporarily unavailable/i],
    [504, /job-search service is temporarily unavailable/i],
    [500, /unable to recover this workspace right now/i],
  ];

  try {
    for (const [status, message] of cases) {
      responseStatus = status;
      await assert.rejects(
        () => recoverLegacyWorkspace({
          recoveryToken: "previous-private-access-key",
          email: "vasu@example.com",
          password: "new-password-kept-exact",
        }),
        message,
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
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
