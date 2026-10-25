import type { NextConfig } from "next";

const backend = new URL(process.env.VM_BACKEND_URL ?? "http://127.0.0.1:8000");
if (
  backend.protocol !== "http:" ||
  !["127.0.0.1", "localhost", "[::1]"].includes(backend.hostname) ||
  backend.username ||
  backend.password ||
  backend.search ||
  backend.hash ||
  backend.pathname !== "/"
) {
  throw new Error("VM_BACKEND_URL必须为本机回环HTTP地址，不包含路径或凭据");
}

const config: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backend.origin}/api/v1/:path*`,
      },
    ];
  },
};
export default config;
