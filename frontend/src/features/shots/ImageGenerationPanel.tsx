"use client";
import { DrawThingsSettings } from "./DrawThingsSettings";
import { ShotGenerationPanel } from "./ShotGenerationPanel";
import type { ProjectRead, ShotRead } from "@/lib/api";
export function ImageGenerationPanel(props: {
  project: ProjectRead;
  episodeVersion: number;
  shot: ShotRead;
  onCompleted: () => void;
}) {
  return (
    <ShotGenerationPanel {...props} kind="image">
      <DrawThingsSettings onSaved={props.onCompleted} />
    </ShotGenerationPanel>
  );
}
