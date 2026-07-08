"use client";

/**
 * Home page (handbook Phase 14, item 1): upload zone + target size + scenario → /task/[taskId].
 * The frontend makes no compression decisions (concern 1) — target and scenario are
 * just user preferences passed straight through to the backend.
 */

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { useDropzone } from "react-dropzone";

import { normalizeError, startCompression, uploadPdf } from "@/lib/api";
import { presetForTarget, useCompressorStore } from "@/lib/store";
import type { CompressionTarget, UiError } from "@/lib/types";

const TARGETS = [5, 10, 15, 20] as const;
const SCENARIOS: { value: CompressionTarget; label: string; hint: string }[] = [
  { value: "screen", label: "Screen viewing", hint: "Everyday sharing, balances quality and size" },
  { value: "print", label: "Print", hint: "Most conservative, prioritizes sharpness" },
  { value: "email", label: "Email attachment", hint: "Maximum compression, size first" },
];

// User-facing summary of the pipeline (docs/系统架构设计.md §3.2 & §3.5), plain English.
const STEPS: { title: string; body: string }[] = [
  {
    title: "Analyze",
    body: "We split your PDF page by page and read every element — photos, vector graphics, text, and embedded fonts.",
  },
  {
    title: "Classify",
    body: "Each page is sorted into a type (photo-heavy, text-heavy, chart, or mixed). You review and correct the results before anything is compressed.",
  },
  {
    title: "Decide",
    body: "A pure decision engine picks the right quality, resolution, and sampling for every element. It's area-aware — a small logo and a full-page render are treated differently — and only ever reduces, never upscales.",
  },
  {
    title: "Compress & converge",
    body: "We compress, measure the real size, and repeat up to 5 times with a binary search until the file lands within ±15% of your target.",
  },
  {
    title: "Smart fallback",
    body: "If your target is very tight, we step down through tiers — keep everything sharp, then rasterize the heaviest pages, then rasterize everything — and always tell you which one was used.",
  },
  {
    title: "Rebuild & clean up",
    body: "We reassemble a real PDF: text stays selectable, vectors stay crisp, and bookmarks and hyperlinks are preserved. Your file is never stored — the download link expires after 5 minutes.",
  },
];

export default function HomePage() {
  const router = useRouter();
  const store = useCompressorStore();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<UiError | null>(null);

  const onDrop = useCallback(
    async (accepted: File[]) => {
      const file = accepted[0];
      if (!file) return;
      setError(null);
      try {
        setBusy("Uploading…");
        const { file_id, size_mb } = await uploadPdf(file);
        store.setUpload(file_id, size_mb);
        setBusy("Starting compression…");
        const { task_id } = await startCompression(
          file_id,
          store.targetSizeMb,
          store.compressionTarget
        );
        store.setTask(task_id, "PENDING");
        router.push(`/task/${task_id}`);
      } catch (err) {
        setError(normalizeError(err));
        setBusy(null);
      }
    },
    [router, store]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 1,
    disabled: busy !== null,
  });

  return (
    <div className="space-y-10">
      <section className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight">
          Compress your portfolio
        </h2>
        <p className="max-w-2xl text-zinc-500 dark:text-zinc-400">
          Pick a target size and scenario, then drop in a PDF. Text, vectors,
          bookmarks, and hyperlinks are kept structurally intact.
        </p>
      </section>

      {/* Target size */}
      <section>
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Target size
        </h3>
        <div className="flex flex-wrap gap-3">
          {TARGETS.map((mb) => (
            <button
              key={mb}
              data-testid={`target-${mb}`}
              onClick={() => store.setTarget(mb, presetForTarget(mb))}
              className={`rounded-lg border px-5 py-2.5 text-sm font-medium transition
                ${
                  store.targetSizeMb === mb
                    ? "border-accent bg-accent text-accent-fg"
                    : "border-zinc-300 hover:border-accent dark:border-zinc-700"
                }`}
            >
              {mb} MB
            </button>
          ))}
        </div>
      </section>

      {/* Compression scenario */}
      <section>
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Scenario
        </h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {SCENARIOS.map((s) => (
            <button
              key={s.value}
              data-testid={`scenario-${s.value}`}
              onClick={() => store.setTarget(store.targetSizeMb, s.value)}
              className={`rounded-lg border p-4 text-left transition
                ${
                  store.compressionTarget === s.value
                    ? "border-accent ring-1 ring-accent"
                    : "border-zinc-300 hover:border-accent dark:border-zinc-700"
                }`}
            >
              <div className="font-medium">{s.label}</div>
              <div className="mt-1 text-xs text-zinc-500">{s.hint}</div>
            </button>
          ))}
        </div>
      </section>

      {/* Upload zone */}
      <section
        {...getRootProps()}
        data-testid="dropzone"
        className={`flex min-h-52 cursor-pointer flex-col items-center justify-center
          rounded-xl border-2 border-dashed p-8 text-center transition
          ${
            isDragActive
              ? "border-accent bg-zinc-50 dark:bg-zinc-900"
              : "border-zinc-300 hover:border-accent dark:border-zinc-700"
          }`}
      >
        <input {...getInputProps()} data-testid="file-input" />
        {busy ? (
          <p className="animate-pulse font-medium text-accent">{busy}</p>
        ) : (
          <>
            <p className="font-medium">Drop a PDF here, or click to choose a file</p>
            <p className="mt-2 text-sm text-zinc-500">Up to 500 MB</p>
          </>
        )}
      </section>

      {error && (
        <div
          data-testid="home-error"
          className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
        >
          {error.message}
        </div>
      )}

      {/* How compression works */}
      <section className="border-t border-zinc-200 pt-10 dark:border-zinc-800">
        <h3 className="text-xl font-semibold tracking-tight">
          How the compression works
        </h3>
        <p className="mt-1 max-w-2xl text-sm text-zinc-500">
          Six steps from your raw portfolio to a file that hits your target —
          without throwing away the things that make a portfolio readable.
        </p>
        <ol className="mt-6 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          {STEPS.map((step, i) => (
            <li key={step.title} className="flex gap-4">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-zinc-300 text-sm font-semibold tabular-nums dark:border-zinc-700">
                {i + 1}
              </span>
              <div className="space-y-1">
                <div className="font-medium">{step.title}</div>
                <p className="text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
                  {step.body}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
