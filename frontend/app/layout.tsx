import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "作品集 PDF 压缩",
  description: "把大体积作品集压到目标大小，保留矢量与文字清晰度",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        <header className="border-b border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800">
          <div className="mx-auto max-w-5xl px-6 py-4 flex items-center gap-3">
            <span className="inline-block h-3 w-3 rounded-full bg-accent" />
            <h1 className="text-lg font-semibold tracking-tight">
              作品集 PDF 压缩
            </h1>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
