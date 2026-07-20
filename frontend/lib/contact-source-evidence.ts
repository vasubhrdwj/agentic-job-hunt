/** Honest threshold labels for a non-probabilistic source-evidence heuristic. */
export function sourceEvidenceThresholdLabel(confidence: number): string {
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    return "Unknown source-evidence result";
  }
  return confidence >= 0.75
    ? "Meets source-evidence threshold"
    : "Below source-evidence threshold";
}

export function sourceQualifiedRationale(
  rationale: string,
  leadKind = "public-source",
): string {
  const normalized = rationale.replace(/\s+/g, " ").trim();
  if (normalized.startsWith("The saved public-search result ")) return normalized;
  const normalizedKind = leadKind.replace(/\s+/g, " ").trim().toLowerCase();
  return `This ${normalizedKind || "public-source"} lead was selected from public search evidence for this role. Review the source before relying on the title or employer.`;
}
