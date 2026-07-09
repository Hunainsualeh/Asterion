import PipelineView from "@/app/components/PipelineView";

export default async function PipelinePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <main className="h-full min-h-0 min-w-0">
      <PipelineView projectId={id} />
    </main>
  );
}
