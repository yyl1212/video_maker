import { notFound } from "next/navigation";
import { AssetWorkspace } from "@/features/assets/AssetWorkspace";

export default async function Page({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const id = Number(projectId);
  if (!Number.isSafeInteger(id) || id < 1) notFound();
  return <AssetWorkspace key={id} projectId={id} />;
}
