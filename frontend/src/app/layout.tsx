import type { Metadata } from "next";
import Link from "next/link";
import { UnsavedChangesGuard } from "@/components/UnsavedChangesGuard";
import "./globals.css";

export const metadata: Metadata = {
  title: "映序 · 本地视频工坊",
  description: "从剧本开始，在本机完成你的故事。",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <UnsavedChangesGuard />
        <div className="app-shell">
          <aside className="sidebar">
            <Link href="/" className="brand">
              <span className="brand-mark" aria-hidden="true">
                映
              </span>
              <span>
                映序<small>本地视频工坊</small>
              </span>
            </Link>
            <p className="nav-label">创作空间</p>
            <nav aria-label="主导航">
              <Link href="/" className="nav-item">
                <span aria-hidden="true">▦</span>我的项目
              </Link>
            </nav>
            <div className="sidebar-note">
              <span className="eyebrow">故事在这里生长</span>
              <p>
                从一段文字，
                <br />
                走向一段影像。
              </p>
              <div className="sidebar-lines" aria-hidden="true" />
            </div>
            <div className="sidebar-footer">
              <span className="live-dot" />
              本机运行<small>创作数据保存在本地</small>
            </div>
          </aside>
          <div className="main-shell">
            <div className="topbar">
              <span>
                创作工作台 <span className="muted">/ 项目与剧集</span>
              </span>
              <span className="local-badge">● 本地创作</span>
            </div>
            <main>{children}</main>
            <div className="app-footer">
              映序 · 留住故事的每一个起点<span>M1 · 项目与剧集</span>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
