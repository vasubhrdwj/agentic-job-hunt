export interface DailyDigestHighlight {
  opportunity_id: string;
  company: string;
  title: string;
  fit_band: "strong" | "promising";
  confidence: "high" | "medium" | "low";
  reasons: string[];
  discovered_at: string;
}

export interface DailyDigestResponse {
  data_source: "database";
  local_date: string;
  timezone: string;
  period_started_at: string;
  generated_at: string;
  headline: string;
  new_opportunities: number;
  evaluated_opportunities: number;
  worth_your_time: number;
  assessment_complete: boolean;
  highlights: DailyDigestHighlight[];
  scans: {
    scheduled: number;
    running: number;
    succeeded: number;
    partial: number;
    failed: number;
  };
  active_scheduled_searches: number;
  next_scan_at: string | null;
}
