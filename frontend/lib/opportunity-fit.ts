export type OpportunityFitBand =
  | "strong"
  | "promising"
  | "stretch"
  | "low"
  | "insufficient_data";

export interface OpportunityFitPresentation {
  label: string;
  guidance: string;
  badgeClasses: string;
  panelClasses: string;
}

const PRESENTATIONS: Record<OpportunityFitBand, OpportunityFitPresentation> = {
  strong: {
    label: "Strong match",
    guidance: "Prioritize this role. Your saved target, résumé, and approved evidence align well; verify the remaining checks before applying.",
    badgeClasses: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
    panelClasses: "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/20",
  },
  promising: {
    label: "Promising",
    guidance: "Worth a closer look. There is useful evidence alignment, with some requirements or preferences still to verify.",
    badgeClasses: "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200",
    panelClasses: "border-blue-200 bg-blue-50/60 dark:border-blue-900 dark:bg-blue-950/20",
  },
  stretch: {
    label: "Stretch",
    guidance: "Consider selectively. The role is adjacent to your target, but important skill or seniority gaps need an honest review.",
    badgeClasses: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
    panelClasses: "border-amber-200 bg-amber-50/60 dark:border-amber-900 dark:bg-amber-950/20",
  },
  low: {
    label: "Low match",
    guidance: "Probably skip unless you have relevant context missing from your profile. The saved target or preferences conflict with this role.",
    badgeClasses: "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200",
    panelClasses: "border-rose-200 bg-rose-50/60 dark:border-rose-900 dark:bg-rose-950/20",
  },
  insufficient_data: {
    label: "Not enough data",
    guidance: "The posting does not contain enough useful evidence for a reliable recommendation. Review the source before deciding.",
    badgeClasses: "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
    panelClasses: "border-zinc-200 bg-zinc-50/60 dark:border-zinc-800 dark:bg-zinc-950/30",
  },
};

export function opportunityFitPresentation(
  band: OpportunityFitBand,
): OpportunityFitPresentation {
  return PRESENTATIONS[band];
}
