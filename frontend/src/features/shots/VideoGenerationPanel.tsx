"use client";
import { VideoSettings } from "./VideoSettings";
import { ShotGenerationPanel } from "./ShotGenerationPanel";
import type { ProjectRead, ShotRead } from "@/lib/api";
export function VideoGenerationPanel(props: {
  project: ProjectRead;
  episodeVersion: number;
  shot: ShotRead;
  onCompleted: () => void;
}) {
  return (
    <ShotGenerationPanel {...props} kind="video">
      <VideoSettings onSaved={props.onCompleted} />
    </ShotGenerationPanel>
  );
}
