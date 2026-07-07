import { OutcomesView } from "@/components/outcomes-view";

type OutcomesPageProps = {
  params: Promise<{ runId: string }>;
};

export default async function OutcomesPage(props: OutcomesPageProps) {
  const { runId } = await props.params;
  return <OutcomesView runId={runId} />;
}
