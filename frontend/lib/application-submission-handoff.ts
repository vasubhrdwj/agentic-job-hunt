import type { ApplicationSubmissionResponse } from "./application-submission-types";

type PersistedDestinationProjection = Pick<
  ApplicationSubmissionResponse,
  "available_destinations" | "first_party_verified"
>;

export function persistedVerifiedDestination(
  projection: PersistedDestinationProjection | null,
  candidate: string,
): string | null {
  if (!projection?.first_party_verified) return null;
  if (!projection.available_destinations.includes(candidate)) return null;
  try {
    return new URL(candidate).protocol === "https:" ? candidate : null;
  } catch {
    return null;
  }
}
