import type { TodaySort } from "./opportunity-types";

export const DEFAULT_TODAY_SORT: TodaySort = "recommended";

export function parseTodaySort(value: string | null): TodaySort {
  return value === "newest" ? "newest" : DEFAULT_TODAY_SORT;
}

export function todaySortExplanation(sort: TodaySort): string {
  if (sort === "newest") {
    return "Newest shows recently surfaced roles first while rotating companies.";
  }
  return "Recommended ranks every matching saved role by actionability, eligibility, fit band, and confidence before pagination. Within the same assessment tier, it demotes listings older than 45 days, favors fresher roles, and—only after enough watch, pursue, and relevant dismiss decisions—uses learned title categories as a tie-breaker. Companies rotate within equal recommendation tiers.";
}
