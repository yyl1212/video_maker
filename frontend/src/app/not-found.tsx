import Link from "next/link";

export default function NotFound() {
  return (
    <div className="empty-state">
      <p className="eyebrow">没有找到这里的故事</p>
      <h1>页面不存在</h1>
      <Link href="/" className="button secondary">
        返回全部项目
      </Link>
    </div>
  );
}
