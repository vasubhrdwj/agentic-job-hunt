import type { CareerTrack, CareerTrackCreate } from "./workspace-types";

function sameStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export function careerTrackMatchesPayload(
  track: CareerTrack,
  payload: CareerTrackCreate,
): boolean {
  return (
    track.name === payload.name &&
    sameStrings(track.role_families, payload.role_families) &&
    sameStrings(track.seniority_levels, payload.seniority_levels) &&
    sameStrings(track.target_locations, payload.target_locations) &&
    track.active === payload.active &&
    track.priorities.compensation === payload.priorities.compensation &&
    track.priorities.scope === payload.priorities.scope &&
    track.priorities.learning === payload.priorities.learning &&
    track.priorities.company_quality === payload.priorities.company_quality &&
    track.priorities.flexibility === payload.priorities.flexibility
  );
}
