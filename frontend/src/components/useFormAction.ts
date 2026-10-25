"use client";

import { useEffect, useRef, useState } from "react";

export function useFormAction<T>(scope: string, onSaved: (value: T) => void) {
  const [state, setState] = useState({
    scope,
    pending: false,
    error: "",
    message: "",
  });
  const activeScope = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    activeScope.current = scope;
    return () => {
      activeScope.current = null;
      controller.current?.abort();
    };
  }, [scope]);

  async function run(
    action: (signal: AbortSignal) => Promise<T>,
    success: string,
  ) {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setState({ scope, pending: true, error: "", message: "" });
    try {
      const result = await action(request.signal);
      // 取消只阻止旧响应进入页面，不能假定已取消后端写入；重试须重新核对版本。
      if (activeScope.current !== scope || request.signal.aborted) return;
      onSaved(result);
      setState({ scope, pending: false, error: "", message: success });
    } catch (error) {
      if (activeScope.current !== scope || request.signal.aborted) return;
      setState({
        scope,
        pending: false,
        error: error instanceof Error ? error.message : "保存失败，请重试",
        message: "",
      });
    }
  }

  function clearFeedback() {
    setState({ scope, pending: false, error: "", message: "" });
  }

  return {
    ...(state.scope === scope
      ? state
      : { pending: false, error: "", message: "" }),
    run,
    clearFeedback,
  };
}
