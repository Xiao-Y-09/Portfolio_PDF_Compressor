import type { Metadata } from "next";
import { Nunito_Sans } from "next/font/google";
import "./globals.css";

// Self-hosted at build time by next/font (no runtime request to Google); exposed as a CSS
// variable that tailwind.config.ts puts at the front of `font-sans`. next/font has no
// fallback metrics for Nunito Sans, so the stack's own fallbacks cover the swap period.
const nunitoSans = Nunito_Sans({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  display: "swap",
  variable: "--font-nunito-sans",
  adjustFontFallback: false,
});

export const metadata: Metadata = {
  title: "Portfolio PDF Compressor",
  description:
    "Compress large portfolios to a target size while keeping vectors and text crisp.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={nunitoSans.variable}>
      <body className="min-h-screen antialiased">
        <header className="sticky top-0 z-10 border-b border-zinc-200/70 bg-white/80 backdrop-blur dark:border-zinc-800/70 dark:bg-zinc-950/80">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-4">
            <span className="inline-block h-4 w-4 rounded-sm bg-accent" />
            <h1 className="text-base font-bold tracking-[-0.01em]">
              Portfolio PDF Compressor
            </h1>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
