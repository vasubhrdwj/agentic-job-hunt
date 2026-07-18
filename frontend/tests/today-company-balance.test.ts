import assert from "node:assert/strict";
import test from "node:test";

import { balanceTodayCompanies } from "../lib/today-company-balance";

function role(id: string, company: string, companySlug: string) {
  return {
    id,
    posting: {
      company,
      company_slug: companySlug,
    },
  };
}

test("Today keeps at most two roles per company while preserving source order", () => {
  const items = [
    role("amazon-1", "Amazon", "amazon"),
    role("stable-1", "Stable Money", "stable-money"),
    role("amazon-2", "Amazon", "amazon"),
    role("zeta-1", "Zeta", "zeta"),
    role("amazon-3", "Amazon", "amazon"),
    role("amazon-4", "Amazon Jobs", "amazon"),
  ];

  const result = balanceTodayCompanies(items, new Set());

  assert.deepEqual(result.visibleItems.map((item) => item.id), [
    "amazon-1",
    "stable-1",
    "amazon-2",
    "zeta-1",
  ]);
  assert.deepEqual(result.overflows, [{
    company: "Amazon",
    companySlug: "amazon",
    hiddenCount: 2,
    totalCount: 4,
  }]);
});

test("removing a visible decision promotes the next role from that company", () => {
  const items = [
    role("amazon-1", "Amazon", "amazon"),
    role("amazon-2", "Amazon", "amazon"),
    role("amazon-3", "Amazon", "amazon"),
  ];

  const afterDecision = balanceTodayCompanies(
    items.filter((item) => item.id !== "amazon-1"),
    new Set(),
  );

  assert.deepEqual(afterDecision.visibleItems.map((item) => item.id), [
    "amazon-2",
    "amazon-3",
  ]);
  assert.deepEqual(afterDecision.overflows, []);
});

test("an expanded company reveals only its own overflow", () => {
  const items = [
    role("amazon-1", "Amazon", "amazon"),
    role("amazon-2", "Amazon", "amazon"),
    role("amazon-3", "Amazon", "amazon"),
    role("zeta-1", "Zeta", "zeta"),
    role("zeta-2", "Zeta", "zeta"),
    role("zeta-3", "Zeta", "zeta"),
  ];

  const result = balanceTodayCompanies(items, new Set(["amazon"]));

  assert.deepEqual(result.visibleItems.map((item) => item.id), [
    "amazon-1",
    "amazon-2",
    "amazon-3",
    "zeta-1",
    "zeta-2",
  ]);
  assert.deepEqual(result.overflows.map((group) => group.companySlug), [
    "amazon",
    "zeta",
  ]);
});

test("loaded pagination increases visible diversity without losing company overflow", () => {
  const firstPage = [
    role("amazon-1", "Amazon", "amazon"),
    role("stable-1", "Stable Money", "stable-money"),
    role("amazon-2", "Amazon", "amazon"),
    role("amazon-3", "Amazon", "amazon"),
  ];
  const appended = [
    role("zeta-1", "Zeta", "zeta"),
    role("amazon-4", "Amazon", "amazon"),
  ];

  const first = balanceTodayCompanies(firstPage, new Set());
  const afterLoadMore = balanceTodayCompanies([...firstPage, ...appended], new Set());

  assert.deepEqual(first.visibleItems.map((item) => item.id), [
    "amazon-1",
    "stable-1",
    "amazon-2",
  ]);
  assert.equal(first.overflows[0]?.hiddenCount, 1);
  assert.deepEqual(afterLoadMore.visibleItems.map((item) => item.id), [
    "amazon-1",
    "stable-1",
    "amazon-2",
    "zeta-1",
  ]);
  assert.equal(afterLoadMore.overflows[0]?.hiddenCount, 2);
});

test("the company limit rejects invalid configuration", () => {
  assert.throws(() => balanceTodayCompanies([], new Set(), 0), RangeError);
});
