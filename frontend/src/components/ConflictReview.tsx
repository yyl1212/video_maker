"use client";

import { useState, type ReactNode } from "react";
import { useFormAction } from "./useFormAction";

type Snapshot = {
  version: number;
  archived_at?: string | null;
  read_only?: boolean;
};
type Props<T extends Snapshot> = {
  scope: string;
  load: (signal: AbortSignal) => Promise<T>;
  describe: (value: T) => ReactNode;
  onUse: (value: T) => void;
};

export function ConflictReview<T extends Snapshot>({
  scope,
  load,
  describe,
  onUse,
}: Props<T>) {
  const [snapshot, setSnapshot] = useState<{ scope: string; value: T } | null>(
    null,
  );
  const action = useFormAction<T>(scope, (value) =>
    setSnapshot({ scope, value }),
  );
  const current = snapshot?.scope === scope ? snapshot.value : null;
  return (
    <div className="conflict-review">
      <button
        type="button"
        className="button secondary small"
        disabled={action.pending}
        onClick={() => void action.run(load, "已读取最新版本")}
      >
        {action.pending ? "正在读取…" : "核对最新版本"}
      </button>
      {action.error && <p className="notice error">{action.error}</p>}
      {current && (
        <section className="latest-snapshot" aria-label="最新版本对照">
          <h3>最新版本 · {current.version}</h3>
          {describe(current)}
          <p className="muted">
            上方的本页草稿保持不变。核对后，明确选择使用此版本作为下一次保存的基线。
          </p>
          {current.archived_at || current.read_only ? (
            <p className="notice">
              内容已在其他页面归档，请返回项目重新读取并恢复。
            </p>
          ) : (
            <button
              type="button"
              className="button secondary small"
              onClick={() => onUse(current)}
            >
              保留草稿并使用此版本
            </button>
          )}
        </section>
      )}
    </div>
  );
}
