import { secondaryButtonClasses } from "./workspace-ui";

export function ApprovedResumeDownload({
  applicationId,
  available,
  label = "Download application résumé (.docx)",
}: {
  applicationId: string;
  available: boolean;
  label?: string;
}) {
  if (!available) return null;
  return (
    <a
      href={`/api/applications/${encodeURIComponent(applicationId)}/application-artifacts/approved-resume.docx`}
      download
      className={secondaryButtonClasses}
    >
      {label}
    </a>
  );
}
