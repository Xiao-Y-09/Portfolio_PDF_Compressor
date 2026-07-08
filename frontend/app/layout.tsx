import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Portfolio PDF Compressor",
  description:
    "Compress large portfolios to a target size while keeping vectors and text crisp.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="sticky top-0 z-10 border-b border-zinc-200/70 bg-white/80 backdrop-blur dark:border-zinc-800/70 dark:bg-zinc-950/80">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-4">
            <span className="inline-block h-4 w-4 rounded-sm bg-accent" />
            <h1 className="text-base font-semibold tracking-tight">
              Portfolio PDF Compressor
            </h1>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
