import type { Seniority } from "./types";

export const DEFAULT_CAREER_TARGET_SENIORITIES: Seniority[] = ["junior", "mid"];

const EARLY_CAREER_ORDER: Seniority[] = ["junior", "mid", "senior", "staff"];
const MID_CAREER_ORDER: Seniority[] = ["mid", "junior", "senior", "staff"];
const EXPERIENCED_ORDER: Seniority[] = ["senior", "mid", "staff", "junior"];

export function preferredSavedSearchSeniority(
  allowed: Seniority[],
  yearsOfExperience: number | null | undefined,
): Seniority {
  if (allowed.length === 0) return "junior";

  const order =
    yearsOfExperience == null || yearsOfExperience < 2
      ? EARLY_CAREER_ORDER
      : yearsOfExperience < 5
        ? MID_CAREER_ORDER
        : EXPERIENCED_ORDER;

  return order.find((level) => allowed.includes(level)) ?? allowed[0];
}

export function careerTrackSearchPrefill(values: string[]): string {
  return values
    .map((value) => value.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(", ");
}
