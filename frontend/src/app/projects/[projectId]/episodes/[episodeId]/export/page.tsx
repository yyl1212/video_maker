import { notFound } from "next/navigation";
import { ExportTimeline } from "@/features/exports/ExportTimeline";

export default async function ExportPage({
  params,
}: {
  params: Promise<{ projectId: string; episodeId: string }>;
}) {
  const { projectId, episodeId } = await params;
  if (
    [projectId, episodeId].some(
      (value) =>
        !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)),
    )
  )
    notFound();
  return (
    <ExportTimeline
      key={`${projectId}:${episodeId}`}
      projectId={Number(projectId)}
      episodeId={Number(episodeId)}
    />
  );
}
