import assert from "node:assert/strict";
import test from "node:test";

import { wakeSleepingBackend } from "../lib/cadence-wake";


const SECRET = "cadence-test-secret-that-is-long-enough-123";
const environment = {
  NODE_ENV: "production",
  API_BASE_URL: "https://job-hunt-agent.onrender.com",
  CRON_SECRET: SECRET,
};

test("cadence wake fails closed before contacting Render", async () => {
  let calls = 0;
  const fetcher: typeof fetch = async () => {
    calls += 1;
    throw new Error("must not call upstream");
  };

  const missing = await wakeSleepingBackend(
    new Request("https://app.example/api/internal/cadence"),
    environment,
    fetcher,
  );
  const wrong = await wakeSleepingBackend(
    new Request("https://app.example/api/internal/cadence", {
      headers: { Authorization: "Bearer wrong" },
    }),
    environment,
    fetcher,
  );

  assert.equal(missing.status, 401);
  assert.equal(wrong.status, 401);
  assert.equal(calls, 0);
});

test("authenticated cadence wake forwards one secret-bearing POST", async () => {
  let calls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    calls += 1;
    assert.equal(String(input), "https://job-hunt-agent.onrender.com/internal/cadence/tick");
    assert.equal(init?.method, "POST");
    assert.equal(new Headers(init?.headers).get("authorization"), `Bearer ${SECRET}`);
    return Response.json({ created_scans: 2, replayed_scans: 0 });
  };
  const response = await wakeSleepingBackend(
    new Request("https://app.example/api/internal/cadence", {
      headers: { Authorization: `Bearer ${SECRET}` },
    }),
    environment,
    fetcher,
  );

  assert.equal(response.status, 200);
  assert.equal(calls, 1);
  assert.deepEqual(await response.json(), { created_scans: 2, replayed_scans: 0 });
});

test("backend rejection is sanitized and retryable", async () => {
  const response = await wakeSleepingBackend(
    new Request("https://app.example/api/internal/cadence", {
      headers: { Authorization: `Bearer ${SECRET}` },
    }),
    environment,
    async () => Response.json({ detail: "PRIVATE DATABASE DETAIL" }, { status: 503 }),
  );

  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.code, "cadence_backend_rejected");
  assert.equal(JSON.stringify(body).includes("PRIVATE"), false);
});
