import { create } from "zustand";
import type { Character, ShotEditable, ReferenceInput } from "@/lib/api";
import type { components } from "@/lib/api-types";

export type ProjectDraft = {
  kind: "project";
  value: { name: string; style_anchor: string; characters: Character[] };
  baseVersion: number;
  dirty: boolean;
};
export type EpisodeDraft = {
  kind: "episode";
  value: { title: string; script_text: string };
  baseVersion: number;
  dirty: boolean;
};
export type ShotDraft = {
  kind: "shot";
  value: ShotEditable;
  baseVersion: number;
  dirty: boolean;
};
export type ReferenceDraft = {
  kind: "references";
  value: ReferenceInput[];
  baseVersion: number;
  dirty: boolean;
};
export type ExportDraft = {
  kind: "export";
  value: {
    source: components["schemas"]["ExportPreviewRead"];
    selected: number[];
    acknowledgements: Record<number, string>;
    profile: NonNullable<
      components["schemas"]["ExportSubmit"]["output_profile"]
    >;
  };
  baseVersion: number;
  dirty: boolean;
};
type EditorDraft =
  ProjectDraft | EpisodeDraft | ShotDraft | ReferenceDraft | ExportDraft;
type WorkbenchState = {
  drafts: Record<string, EditorDraft>;
  setDraft: (key: string, draft: EditorDraft) => void;
  clearDraft: (key: string) => void;
};

// 以实体作用域保存草稿；服务器版本仅在明确保存后更新，不能被轮询升级。
export const useWorkbenchStore = create<WorkbenchState>((set) => ({
  drafts: {},
  setDraft: (key, draft) =>
    set((state) => ({ drafts: { ...state.drafts, [key]: draft } })),
  clearDraft: (key) =>
    set((state) => {
      const drafts = { ...state.drafts };
      delete drafts[key];
      return { drafts };
    }),
}));
