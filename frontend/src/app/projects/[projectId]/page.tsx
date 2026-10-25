import { notFound } from "next/navigation";
import { ProjectWorkspace } from "@/features/projects/ProjectWorkspace";

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  if (!/^[1-9]\d*$/.test(projectId) || !Number.isSafeInteger(Number(projectId)))
    notFound();
  return <ProjectWorkspace key={projectId} projectId={Number(projectId)} />;
}
