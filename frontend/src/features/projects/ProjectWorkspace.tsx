"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest, type EpisodeRead, type ProjectRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";
import { EpisodeForm } from "./EpisodeForm";
import { ProjectForm } from "./ProjectForm";

export function ProjectWorkspace({ projectId }: { projectId: number }) {
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeRead[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const action = useFormAction<ProjectRead>(
    `archive-project:${projectId}`,
    setProject,
  );
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      apiRequest<ProjectRead>(`/projects/${projectId}`, {
        signal: controller.signal,
      }),
      apiRequest<EpisodeRead[]>(`/projects/${projectId}/episodes`, {
        signal: controller.signal,
      }),
    ])
      .then(([current, items]) => {
        if (!controller.signal.aborted) {
          setProject(current);
          setEpisodes(items);
        }
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
  if (!project) return <p className="muted">正在读取项目…</p>;

  return (
    <>
      <Link className="back-link" href="/">
        ← 全部项目
      </Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">项目工作区</p>
          <h1>{project.name}</h1>
          <p className="lede">先把故事、风格与角色准备好。</p>
        </div>
        <button
          className="button secondary"
          disabled={action.pending}
          onClick={() =>
            void action.run(
              (signal) =>
                apiRequest<ProjectRead>(`/projects/${project.id}`, {
                  method: "PATCH",
                  signal,
                  body: {
                    expected_version: project.version,
                    archived: !project.archived_at,
                  },
                }),
              project.archived_at ? "项目已恢复" : "项目已归档",
            )
          }
        >
          {project.archived_at ? "恢复项目" : "归档项目"}
        </button>
      </header>
      <div className="workspace-strip">
        <span>项目素材集中管理，分镜按需引用。</span>
        <Link
          className="button secondary small"
          href={`/projects/${projectId}/assets`}
        >
          打开资产库 →
        </Link>
      </div>
      {(action.error || action.message) && (
        <p
          role={action.error ? "alert" : "status"}
          className={`notice ${action.error ? "error" : "success"}`}
        >
          {action.error || action.message}
        </p>
      )}
      {project.archived_at && (
        <p className="notice">
          项目已归档，全部内容仍可查看。恢复后可继续编辑。
        </p>
      )}
      <div className="workspace-columns">
        <section className="panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">项目的共同语言</p>
              <h2>风格与角色</h2>
            </div>
          </div>
          <ProjectForm project={project} onSaved={setProject} />
        </section>
        <section className="panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">把故事分集讲述</p>
              <h2>
                剧集 <span className="count">{episodes.length}</span>
              </h2>
            </div>
            <button
              className="button secondary small"
              disabled={Boolean(project.archived_at)}
              onClick={() => setCreating(!creating)}
            >
              {creating ? "收起表单" : "新建剧集"}
            </button>
          </div>
          {creating && (
            <div className="inset-form">
              <EpisodeForm
                projectId={projectId}
                readonly={Boolean(project.archived_at)}
                onSaved={(episode) => {
                  setEpisodes((items) => [...items, episode]);
                  setCreating(false);
                }}
              />
            </div>
          )}
          {episodes.length === 0 ? (
            <div className="empty-small">
              <h3>故事从第一集开始</h3>
              <p>新建剧集，写下或粘贴原创剧本。</p>
            </div>
          ) : (
            <div className="episode-list">
              {episodes.map((episode, index) => (
                <Link
                  key={episode.id}
                  href={`/projects/${projectId}/episodes/${episode.id}`}
                  className="episode-card"
                >
                  <span className="episode-index">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div>
                    <h3>{episode.title}</h3>
                    <p>
                      {episode.archived_at
                        ? "已归档"
                        : `${episode.script_text.length.toLocaleString()} 字剧本`}
                    </p>
                  </div>
                  <span className="arrow" aria-hidden="true">
                    →
                  </span>
                </Link>
              ))}
            </div>
          )}
          <p className="panel-note">
            剧本解析、素材与分镜制作会在后续模块开放。
          </p>
        </section>
      </div>
    </>
  );
}
