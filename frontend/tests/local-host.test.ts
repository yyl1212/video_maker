import { expect, test } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";

test.each([
  "external.example:3000",
  "localhost.evil:3000",
  "evil@localhost:3000",
  "127.0.0.1:99999",
])("代理前拒绝非法原始Host %s", (host) => {
  const response = proxy(
    new NextRequest("http://127.0.0.1:3000/api/v1/bootstrap", {
      headers: {
        host,
        "x-forwarded-host": "127.0.0.1:3000",
        "sec-fetch-site": "same-origin",
      },
    }),
  );
  expect(response.status).toBe(403);
});

test.each(["localhost:3000", "127.0.0.1:3000", "[::1]:3000"])(
  "合法回环Host继续转发 %s",
  (host) => {
    const response = proxy(
      new NextRequest("http://127.0.0.1:3000/api/v1/projects", {
        headers: { host },
      }),
    );
    expect(response.headers.get("x-middleware-next")).toBe("1");
  },
);
