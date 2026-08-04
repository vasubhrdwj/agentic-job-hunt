import type { ApplicationArtifactsResponse } from "./application-artifact-types";
import type { ApplicationPackResponse } from "./application-pack-types";
import type { ApplicationStage } from "./application-types";

export type DossierReadinessState =
  | "waiting"
  | "prepared"
  | "needs_review"
  | "approved";

export interface ApplicationDossierFlow {
  coverage: DossierReadinessState;
  materials: DossierReadinessState;
  primaryHref: string;
  primaryLabel: string;
  guidance: string;
}

/**
 * Keep the preparation page honest about what is saved without exposing the
 * pack/artifact state machine to the job seeker. The existing immutable
 * receipts remain authoritative; this only chooses the next human-facing step.
 */
export function applicationDossierFlow({
  stage,
  pack,
  artifacts,
}: {
  stage: ApplicationStage;
  pack: ApplicationPackResponse | null;
  artifacts: ApplicationArtifactsResponse | null;
}): ApplicationDossierFlow {
  const coverage = coverageState(pack);
  const materials = materialsState(artifacts);

  if (stage !== "pursuing") {
    return {
      coverage,
      materials,
      primaryHref: "#hiring-progress-title",
      primaryLabel: "Review current next step",
      guidance: "Preparation is frozen after you leave the pursuing stage; the exact approved records remain available below.",
    };
  }

  if (coverage === "waiting") {
    return {
      coverage,
      materials,
      primaryHref: "#application-pack",
      primaryLabel: "Prepare complete dossier",
      guidance: "The saved role and résumé are being turned into one grounded package. No approval is recorded during preparation.",
    };
  }

  if (coverage !== "approved") {
    return {
      coverage,
      materials,
      primaryHref: "#application-materials",
      primaryLabel: "Review complete dossier",
      guidance: "The fit assessment is only a preview. Review coverage, why-fit, résumé changes, and answers together, then approve the exact package once.",
    };
  }

  if (materials === "waiting" || materials === "prepared") {
    return {
      coverage,
      materials,
      primaryHref: "#application-materials",
      primaryLabel: "Check dossier preparation",
      guidance: "Coverage is saved. The résumé changes, why-fit draft, and any application answers are being prepared from those exact sources.",
    };
  }

  if (materials === "needs_review") {
    return {
      coverage,
      materials,
      primaryHref: "#application-materials",
      primaryLabel: "Review and approve dossier",
      guidance: "The complete application dossier is ready. Scan every block, edit inputs only where needed, then approve the package once.",
    };
  }

  return {
    coverage,
    materials,
    primaryHref: "#application-people",
    primaryLabel: "Prepare people and messages",
    guidance: "The package is approved. Find up to five relevant people, review each prepared message, and submit or send everything yourself.",
  };
}

function coverageState(
  pack: ApplicationPackResponse | null,
): DossierReadinessState {
  if (!pack || pack.status === "not_started") return "waiting";
  if (pack.status === "reviewed") return "approved";
  return pack.current_revision ? "prepared" : "waiting";
}

function materialsState(
  artifacts: ApplicationArtifactsResponse | null,
): DossierReadinessState {
  if (!artifacts || artifacts.status === "not_started") {
    return artifacts?.source_catalog ? "prepared" : "waiting";
  }
  if (artifacts.status === "approved") return "approved";
  return artifacts.current_revision ? "needs_review" : "prepared";
}
