import { notFound } from "next/navigation";
import { EpisodeWorkspace } from "@/features/projects/EpisodeWorkspace";

export default async function EpisodePage({
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
    <EpisodeWorkspace
      key={`${projectId}:${episodeId}`}
      projectId={Number(projectId)}
      episodeId={Number(episodeId)}
    />
  );
}
