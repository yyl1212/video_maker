"use client";
import { type EpisodeRead, type ProjectRead } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench";
import { AssistantTaskPanel } from "./AssistantTaskPanel";
import { TextModelSettings } from "./TextModelSettings";

export function ScriptParser({
  project,
  episode,
  onApplied,
}: {
  project: ProjectRead;
  episode: EpisodeRead;
  onApplied: (result: EpisodeRead) => void;
}) {
  const dirty = useWorkbenchStore((s) =>
    Boolean(s.drafts[`episode:${episode.id}`]?.dirty),
  );
  return (
    <>
      <AssistantTaskPanel
        key={`${project.id}:${episode.id}`}
        kind="PARSE_SCRIPT"
        projectId={project.id}
        episodeId={episode.id}
        projectVersion={project.version}
        episodeVersion={episode.version}
        readonly={Boolean(project.archived_at || episode.archived_at)}
        dirty={dirty}
        ready={Boolean(episode.script_text.trim())}
        onApplied={(result) => {
          if ("project_id" in result) onApplied(result);
        }}
      />
      <TextModelSettings />
    </>
  );
}
