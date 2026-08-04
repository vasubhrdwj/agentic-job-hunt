import type { TodayRecommendationSignals } from "./opportunity-types";

export interface RecommendationSignalLabel {
  kind: "recency" | "preferred" | "deprioritized";
  label: string;
}

export function recommendationSignalLabels(
  signals: TodayRecommendationSignals | null,
): RecommendationSignalLabel[] {
  if (!signals) return [];

  const sourceReported = signals.age_basis === "source_posted_date";
  const recencyLabel = sourceReported
    ? {
        recent: "Posted in the last 7 days",
        current: "Posted 8–21 days ago",
        aging: "Posted 22–45 days ago",
        older_than_45_days: "Older than 45 days",
      }[signals.recency]
    : {
        recent: "Discovered in the last 7 days",
        current: "Discovered 8–21 days ago",
        aging: "Discovered 22–45 days ago",
        older_than_45_days: "First seen over 45 days ago",
      }[signals.recency];
  const labels: RecommendationSignalLabel[] = [
    { kind: "recency", label: recencyLabel },
  ];

  const roles = signals.preference_role_tags.join(" / ");
  if (signals.preference === "preferred" && roles) {
    labels.push({
      kind: "preferred",
      label: `Matches your preferred ${roles} roles`,
    });
  } else if (signals.preference === "deprioritized" && roles) {
    labels.push({
      kind: "deprioritized",
      label: `Your prior decisions deprioritize ${roles} roles`,
    });
  }
  return labels;
}
