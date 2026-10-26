"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest, type ProjectRead } from "@/lib/api";
import { ProjectForm } from "./ProjectForm";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectRead[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ProjectRead[]>("/projects", { signal: controller.signal })
      .then(setProjects)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "无法读取项目");
      });
    return () => controller.abort();
  }, []);

  return (
    <>
      <header className="page-heading">
        <div>
          <p className="eyebrow">你的创作空间</p>
          <h1>下一段故事，从这里开始。</h1>
          <p className="lede">把想法写进剧本，为故事建立自己的风格和角色。</p>
        </div>
        <button
          className="button primary"
          disabled={projects === null}
          onClick={() => setCreating(!creating)}
        >
          {creating ? "收起新建表单" : "新建项目"}
        </button>
      </header>
      <div className="workspace-strip">
        <span className="live-dot" />
        本机工作区
        <span className="strip-divider" />
        {projects?.filter((p) => !p.archived_at).length ?? 0} 个进行中项目
        <span className="muted ml-auto">当前可用 · 分镜、首帧与音画</span>
      </div>
      {creating && (
        <section className="panel new-project">
          <div className="section-heading">
            <div>
              <span className="eyebrow">一个新的开始</span>
              <h2>创建项目</h2>
            </div>
          </div>
          <ProjectForm
            onSaved={(project) => {
              setProjects((items) => [project, ...(items ?? [])]);
              setCreating(false);
            }}
          />
        </section>
      )}
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      {projects === null && !error && (
        <p className="muted">正在读取本机项目…</p>
      )}
      {projects?.length === 0 && (
        <section className="empty-state">
          <div className="empty-art" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <span className="eyebrow">留一点空间给想象</span>
          <h2>还没有项目</h2>
          <p>创建第一个项目，保存全局风格、角色和第一集剧本。</p>
        </section>
      )}
      {projects && projects.length > 0 && (
        <section className="project-grid" aria-label="项目列表">
          {projects.map((project, index) => (
            <Link
              href={`/projects/${project.id}`}
              className={`project-card ${project.archived_at ? "archived" : ""}`}
              key={project.id}
            >
              <div className="card-cover">
                <span className="card-number">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="pill">
                  {project.archived_at ? "已归档" : "创作中"}
                </span>
                <div className="cover-lines" aria-hidden="true" />
              </div>
              <div className="card-body">
                <h2>
                  {project.name}
                  <span aria-hidden="true">↗</span>
                </h2>
                <p>{project.style_anchor || "等待你定义故事的独特风格。"}</p>
                <div className="card-meta">
                  <span>{project.characters.length} 个角色</span>
                  <span>进入项目 →</span>
                </div>
              </div>
            </Link>
          ))}
        </section>
      )}
      <footer className="page-note">
        定义故事、整理参考素材，在分镜工作台逐镜生成首帧和有声视频。
      </footer>
    </>
  );
}
