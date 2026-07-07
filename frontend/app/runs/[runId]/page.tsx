import { RunReview } from "@/components/run-review";

type ReviewPageProps = {
  params: Promise<{ runId: string }>;
};

export default async function ReviewPage(props: ReviewPageProps) {
  const { runId } = await props.params;
  return <RunReview runId={runId} />;
}
