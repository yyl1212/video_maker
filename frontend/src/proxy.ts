import { NextRequest, NextResponse } from "next/server";

function isLocalHost(value: string | null): boolean {
  const match = value?.match(
    /^(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$/i,
  );
  if (!match) return false;
  const port = match[1];
  return port === undefined || (Number(port) >= 1 && Number(port) <= 65535);
}

// 在rewrites改写Host之前检查所有入口，包括同源GET及静态页面。
export function proxy(request: NextRequest) {
  if (!isLocalHost(request.headers.get("host"))) {
    return NextResponse.json(
      { error: { code: "LOCAL_ONLY", message: "请通过本机地址访问工作台" } },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  return NextResponse.next();
}
