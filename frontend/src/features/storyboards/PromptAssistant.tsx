"use client";
import { type ProjectRead, type ShotRead } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench";
import { AssistantTaskPanel } from "./AssistantTaskPanel";

export function PromptAssistant({
  project,
  episodeVersion,
  shot,
  onApplied,
}: {
  project: ProjectRead;
  episodeVersion: number;
  shot: ShotRead;
  onApplied: (result: ShotRead) => void;
}) {
  const dirty = useWorkbenchStore((s) =>
    Boolean(
      s.drafts[`shot:${shot.id}`]?.dirty ||
      s.drafts[`references:${shot.id}`]?.dirty,
    ),
  );
  return (
    <AssistantTaskPanel
      key={`${project.id}:${shot.id}`}
      kind="OPTIMIZE_PROMPTS"
      projectId={project.id}
      episodeId={shot.episode_id}
      shotId={shot.id}
      projectVersion={project.version}
      episodeVersion={episodeVersion}
      shotVersion={shot.version}
      readonly={Boolean(project.archived_at || shot.read_only)}
      dirty={dirty}
      ready={Boolean(shot.raw_description.trim())}
      onApplied={(result) => {
        if ("episode_id" in result) onApplied(result);
      }}
    />
  );
}
