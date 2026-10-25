"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest, type EpisodeRead, type ProjectRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";
import { EpisodeForm } from "./EpisodeForm";
import { ShotList } from "@/features/shots/ShotList";

export function EpisodeWorkspace({
  projectId,
  episodeId,
}: {
  projectId: number;
  episodeId: number;
}) {
  const [episode, setEpisode] = useState<EpisodeRead | null>(null);
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [error, setError] = useState("");
  const refresh = useFormAction<{ episode: EpisodeRead; project: ProjectRead }>(
    `episode-refresh:${episodeId}`,
    (result) => {
      setEpisode(result.episode);
      setProject(result.project);
    },
  );
  const action = useFormAction<EpisodeRead>(
    `archive-episode:${episodeId}`,
    setEpisode,
  );
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      apiRequest<ProjectRead>(`/projects/${projectId}`, {
        signal: controller.signal,
      }),
      apiRequest<EpisodeRead>(`/episodes/${episodeId}`, {
        signal: controller.signal,
      }),
    ])
      .then(([current, item]) => {
        if (controller.signal.aborted) return;
        if (item.project_id !== projectId) {
          setError("剧集不属于当前项目，请返回项目重新选择");
          return;
        }
        setProject(current);
        setEpisode(item);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "无法读取剧集");
      });
    return () => controller.abort();
  }, [projectId, episodeId]);
  if (error)
    return (
      <p role="alert" className="notice error">
        {error}
      </p>
    );
  if (!episode || !project) return <p className="muted">正在读取剧本…</p>;

  return (
    <>
      <Link className="back-link" href={`/projects/${projectId}`}>
        ← {project.name}
      </Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">剧集 / 原始剧本</p>
          <h1>{episode.title}</h1>
          <p className="lede">先忠实记录故事，再逐步编排镜头。</p>
        </div>
        <button
          className="button secondary"
          disabled={action.pending || Boolean(project.archived_at)}
          onClick={() =>
            void action.run(
              (signal) =>
                apiRequest<EpisodeRead>(`/episodes/${episode.id}`, {
                  method: "PATCH",
                  signal,
                  body: {
                    expected_version: episode.version,
                    archived: !episode.archived_at,
                  },
                }),
              episode.archived_at ? "剧集已恢复" : "剧集已归档",
            )
          }
        >
          {episode.archived_at ? "恢复剧集" : "归档剧集"}
        </button>
      </header>
      {(action.error || action.message) && (
        <p
          role={action.error ? "alert" : "status"}
          className={`notice ${action.error ? "error" : "success"}`}
        >
          {action.error || action.message}
        </p>
      )}
      {(episode.archived_at || project.archived_at) && (
        <p className="notice">当前内容已归档，可查看原文，恢复后继续编辑。</p>
      )}
      <div className="script-columns">
        <section className="panel">
          <EpisodeForm
            projectId={projectId}
            episode={episode}
            readonly={Boolean(project.archived_at)}
            onSaved={setEpisode}
          />
        </section>
        <aside className="panel writing-note">
          <span className="eyebrow">给故事一点耐心</span>
          <h2>把画面写清楚</h2>
          <p>地点、人物、动作和对白，是镜头的起点。</p>
          <ol>
            <li>交代故事发生的环境。</li>
            <li>写下人物正在做的事。</li>
            <li>保留对白的原始语言。</li>
          </ol>
          <div className="stage-note">
            当前可用：剧本保存、分镜编排、参考首帧
            <br />
            在下方工作台逐镜编辑与生成首帧，视频和导出随后接入。
          </div>
        </aside>
      </div>
      {refresh.error && (
        <p role="alert" className="notice error">
          {refresh.error}
        </p>
      )}
      <ShotList
        project={project}
        episodeId={episodeId}
        readonly={Boolean(project.archived_at || episode.archived_at)}
        onEpisodeChanged={() =>
          void refresh.run(async (signal) => {
            const [item, current] = await Promise.all([
              apiRequest<EpisodeRead>(`/episodes/${episodeId}`, { signal }),
              apiRequest<ProjectRead>(`/projects/${projectId}`, { signal }),
            ]);
            return { episode: item, project: current };
          }, "剧集版本已同步")
        }
      />
    </>
  );
}
