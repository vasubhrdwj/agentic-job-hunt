import type { TodaySort } from "./opportunity-types";

export const DEFAULT_TODAY_SORT: TodaySort = "recommended";

export function parseTodaySort(value: string | null): TodaySort {
  return value === "newest" ? "newest" : DEFAULT_TODAY_SORT;
}

export function todaySortExplanation(sort: TodaySort): string {
  if (sort === "newest") {
    return "Newest shows recently surfaced roles first while rotating companies.";
  }
  return "Recommended ranks every matching saved role by actionability, eligibility, fit band, and confidence before pagination, then rotates companies within equal-fit tiers.";
}
