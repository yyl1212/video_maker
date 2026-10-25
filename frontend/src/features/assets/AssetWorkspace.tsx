"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest, type ProjectRead } from "@/lib/api";
import { AssetLibrary } from "./AssetLibrary";

export function AssetWorkspace({ projectId }: { projectId: number }) {
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ProjectRead>(`/projects/${projectId}`, {
      signal: controller.signal,
    })
      .then((p) => {
        if (!controller.signal.aborted) setProject(p);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "无法读取项目");
      });
    return () => controller.abort();
  }, [projectId]);
  if (error)
    return (
      <p role="alert" className="notice error">
        {error}
      </p>
    );
  if (!project) return <p>正在读取项目…</p>;
  return (
    <>
      <Link className="back-link" href={`/projects/${projectId}`}>
        ← {project.name}
      </Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">项目 / 资产库</p>
          <h1>故事的视觉素材</h1>
          <p className="lede">积累角色、场景与片段，再把它们带入每一个镜头。</p>
        </div>
      </header>
      {project.archived_at && (
        <p className="notice">项目已归档，素材保持只读。</p>
      )}
      <AssetLibrary
        projectId={projectId}
        readonly={Boolean(project.archived_at)}
      />
    </>
  );
}
