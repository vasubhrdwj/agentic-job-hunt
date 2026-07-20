import type { ApplicationArtifactsResponse } from "./application-artifact-types";
import type { ContactCategory } from "./application-types";
import type { AchievementEvidence } from "./workspace-types";

export const LINKEDIN_FRIENDLY_DRAFT_LIMIT = 500;

export interface ApprovedOutreachGrounding {
  applicationId: string;
  artifactRevisionId: string;
  approvalEventId: string;
  roleTitle: string;
  companyName: string;
  evidence: {
    id: string;
    version: number;
    statement: string;
  };
}

export interface ApprovedProfileOutreachGrounding {
  applicationId: string;
  roleTitle: string;
  companyName: string;
  evidence: {
    id: string;
    version: number;
    statement: string;
  };
}

export type OutreachGrounding =
  | ApprovedOutreachGrounding
  | ApprovedProfileOutreachGrounding;

export interface OutreachRecipientFacts {
  applicationContactId: string;
  publicName: string;
  category: ContactCategory;
  currentTitle: string;
  currentCompany: string;
  whyRelevant: string;
  employerEvidence: {
    excerpt: string;
    source: string;
  };
}

export interface PreparedOutreachDrafts {
  initial: string;
  followUp: string;
  provenance:
    | {
        source: "approved_application_materials";
        artifactRevisionId: string;
        approvalEventId: string;
        evidenceId: string;
        evidenceVersion: number;
      }
    | {
        source: "approved_profile_evidence";
        evidenceId: string;
        evidenceVersion: number;
      };
}

export function approvedOutreachGrounding({
  artifacts,
  applicationId,
  roleTitle,
  companyName,
}: {
  artifacts: ApplicationArtifactsResponse | null;
  applicationId: string;
  roleTitle: string;
  companyName: string;
}): ApprovedOutreachGrounding | null {
  if (
    !artifacts || artifacts.application_id !== applicationId ||
    artifacts.blockers.includes("grounding_evidence_changed")
  ) return null;
  const revision = artifacts.approved_revision;
  const approvalEvent = artifacts.approval_event;
  if (
    !revision || !approvalEvent || approvalEvent.event_type !== "approved" ||
    approvalEvent.artifact_revision_id !== revision.id
  ) {
    return null;
  }

  const compactRole = compact(roleTitle);
  const compactCompany = compact(companyName);
  const evidence = revision.selected_evidence.find((item) => {
    const statement = compact(item.statement);
    return statement.length > 0 && statement.length <= 180;
  });
  if (!compactRole || !compactCompany || !evidence) return null;

  return {
    applicationId,
    artifactRevisionId: revision.id,
    approvalEventId: approvalEvent.id,
    roleTitle: compactRole,
    companyName: compactCompany,
    evidence: {
      id: evidence.id,
      version: evidence.version,
      statement: compact(evidence.statement),
    },
  };
}

export function approvedProfileOutreachGrounding({
  evidence,
  applicationId,
  roleTitle,
  companyName,
}: {
  evidence: AchievementEvidence[];
  applicationId: string;
  roleTitle: string;
  companyName: string;
}): ApprovedProfileOutreachGrounding | null {
  const compactRole = compact(roleTitle);
  const compactCompany = compact(companyName);
  if (!compactRole || !compactCompany) return null;

  const roleTerms = searchTerms(compactRole);
  const candidates = evidence
    .filter((item) => (
      item.approval_state === "approved" &&
      item.approved_at !== null &&
      item.retired_at === null &&
      compact(item.statement).length > 0 &&
      compact(item.statement).length <= 180
    ))
    .map((item) => ({
      item,
      relevance: evidenceRelevance(item, roleTerms),
    }))
    .filter(({ relevance }) => relevance > 0)
    .sort((left, right) => (
      right.relevance - left.relevance ||
      (right.item.approved_at ?? "").localeCompare(left.item.approved_at ?? "") ||
      left.item.id.localeCompare(right.item.id)
    ));
  const selected = candidates[0]?.item;
  if (!selected) return null;

  return {
    applicationId,
    roleTitle: compactRole,
    companyName: compactCompany,
    evidence: {
      id: selected.id,
      version: selected.version,
      statement: compact(selected.statement),
    },
  };
}

export function prepareGroundedOutreachDrafts(
  grounding: OutreachGrounding | null,
  recipient: OutreachRecipientFacts,
): PreparedOutreachDrafts | null {
  if (!grounding) return null;
  const name = compact(recipient.publicName);
  const currentTitle = compact(recipient.currentTitle);
  const currentCompany = compact(recipient.currentCompany);
  const evidenceSource = compact(recipient.employerEvidence.source);
  if (!name || !currentTitle || !currentCompany || !evidenceSource) {
    return null;
  }
  const greetingName = name.split(" ")[0] ?? name;
  const roleOpening = `Hi ${name} — I’m applying for ${grounding.roleTitle} at ${grounding.companyName}. My relevant background: ${grounding.evidence.statement}`;
  const initial = fitDraft(
    [
      `${roleOpening} A public search result pointed me to your profile and described you as ${currentTitle} at ${currentCompany}.`,
      `${roleOpening} A public search result pointed me to your profile while I was researching this team.`,
      `${roleOpening}.`,
    ],
    initialCta(recipient.category),
  );
  const followUp = fitDraft(
    [
      `Hi ${name} — just following up on my note about ${grounding.roleTitle} at ${grounding.companyName}. A public search result described you as ${currentTitle} at ${currentCompany}, so I thought you might be the right person to ask.`,
      `Hi ${name} — just following up on my note about ${grounding.roleTitle} at ${grounding.companyName}.`,
      `Hi ${greetingName} — following up about ${grounding.roleTitle} at ${grounding.companyName}.`,
    ],
    followUpCta(recipient.category),
  );
  if (!initial || !followUp) return null;

  const common = {
    initial,
    followUp,
  };
  if ("artifactRevisionId" in grounding) {
    return {
      ...common,
      provenance: {
        source: "approved_application_materials",
        artifactRevisionId: grounding.artifactRevisionId,
        approvalEventId: grounding.approvalEventId,
        evidenceId: grounding.evidence.id,
        evidenceVersion: grounding.evidence.version,
      },
    };
  }
  return {
    ...common,
    provenance: {
      source: "approved_profile_evidence",
      evidenceId: grounding.evidence.id,
      evidenceVersion: grounding.evidence.version,
    },
  };
}

export function hydrateOutreachDraft({
  currentValue,
  dirty,
  savedBody,
  preparedBody,
}: {
  currentValue: string;
  dirty: boolean;
  savedBody: string | null;
  preparedBody: string;
}): string {
  if (dirty) return currentValue;
  if (savedBody !== null) return savedBody;
  return preparedBody;
}

export function outreachDraftIsDirty({
  value,
  savedBody,
  preparedBody,
}: {
  value: string;
  savedBody: string | null;
  preparedBody: string;
}): boolean {
  return value !== (savedBody ?? preparedBody);
}

function initialCta(category: ContactCategory): string {
  if (category === "recruiter") {
    return "If this background could fit, would you be open to referring or forwarding my application to the hiring team? No pressure—thanks either way.";
  }
  if (category === "team_leader") {
    return "If my background could help the team, would you be comfortable referring me for the role? No pressure—thanks either way.";
  }
  if (category === "team_peer" || category === "adjacent_peer") {
    return "If you think my background could fit, would you be comfortable referring me—or pointing me to the right person? No pressure—thanks either way.";
  }
  return "If you think my background could fit, would you be comfortable referring me—or pointing me to the right person? No pressure—thanks either way.";
}

function followUpCta(category: ContactCategory): string {
  if (category === "recruiter") {
    return "If the role is still active and my background could fit, would you be open to forwarding or referring my application? No worries if you’re busy—thanks.";
  }
  if (category === "team_leader") {
    return "If you think my background could help, would you be comfortable referring me? No worries if you’re busy—thanks.";
  }
  if (category === "team_peer" || category === "adjacent_peer") {
    return "If you think my background could fit, would you be comfortable referring me or pointing me to the right person? No worries if you’re busy—thanks.";
  }
  return "If you think my background could fit, would you be comfortable referring me or pointing me to the right person? No worries if you’re busy—thanks.";
}

function fitDraft(bases: string[], cta: string): string {
  const compactCta = "Would you be comfortable referring me or pointing me to the right person? Thanks.";
  for (const base of bases) {
    for (const ending of [cta, compactCta]) {
      const draft = `${base} ${ending}`;
      if (draft.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT) return draft;
    }
  }
  return "";
}

function compact(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function searchTerms(value: string): Set<string> {
  return new Set(
    value.toLowerCase().match(/[a-z0-9+#.]{2,}/g) ?? [],
  );
}

function evidenceRelevance(
  evidence: AchievementEvidence,
  roleTerms: Set<string>,
): number {
  const evidenceTerms = searchTerms(
    `${evidence.statement} ${evidence.skills.join(" ")}`,
  );
  let overlap = 0;
  for (const term of roleTerms) {
    if (evidenceTerms.has(term)) overlap += 1;
  }
  return overlap;
}
