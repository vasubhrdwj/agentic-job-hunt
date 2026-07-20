import assert from "node:assert/strict";
import test from "node:test";

import { BackendConfigError, backendBaseUrl } from "../lib/backend-url";

test("local development keeps the explicit loopback fallback", () => {
  assert.equal(
    backendBaseUrl({ NODE_ENV: "development" }).href,
    "http://127.0.0.1:8000/",
  );
});

test("production requires an explicit HTTPS backend", () => {
  assert.throws(
    () => backendBaseUrl({ NODE_ENV: "production" }),
    (error) => (
      error instanceof BackendConfigError &&
      error.message === "API_BASE_URL is required in production"
    ),
  );
  assert.throws(
    () => backendBaseUrl({
      NODE_ENV: "production",
      API_BASE_URL: "http://job-hunt-agent.onrender.com",
    }),
    /must use https in production/,
  );
});

test("production accepts and normalizes the exact HTTPS Render origin", () => {
  assert.equal(
    backendBaseUrl({
      NODE_ENV: "production",
      API_BASE_URL: " https://job-hunt-agent.onrender.com ",
    }).href,
    "https://job-hunt-agent.onrender.com/",
  );
});

test("backend URLs cannot embed credentials", () => {
  assert.throws(
    () => backendBaseUrl({
      NODE_ENV: "production",
      API_BASE_URL: "https://user:secret@job-hunt-agent.onrender.com",
    }),
    /must not include credentials/,
  );
});
