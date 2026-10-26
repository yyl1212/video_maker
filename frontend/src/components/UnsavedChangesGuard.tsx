"use client";

import { useEffect } from "react";
import { useWorkbenchStore } from "@/stores/workbench";

export function UnsavedChangesGuard() {
  const dirty = useWorkbenchStore((state) =>
    Object.values(state.drafts).some((draft) => draft.dirty),
  );
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  return null;
}
