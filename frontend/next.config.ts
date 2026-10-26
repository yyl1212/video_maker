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
  agentRules: false,
  experimental: {
    // Next代理会克隆请求体；默认10MiB会截断产品允许的视频上传。
    proxyClientMaxBodySize: 513 * 1024 * 1024,
  },
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
