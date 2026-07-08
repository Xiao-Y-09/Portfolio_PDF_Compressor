"use client";

/**
 * AWAITING_REVIEW 视图（关注点 2，Phase 14 的关键交互）：
 * 页面网格（缩略图 + 分类标签 + 修改控件）；complexity_score 高的页面
 * 高亮"请确认"；skip/aggressive 页级标记；确认后触发 /resume。
 * 光栅化知情提示（架构设计 §4.5 前端预告）。
 */

import { useState } from "react";

import { thumbnailUrl } from "@/lib/api";
import { useCompressorStore } from "@/lib/store";
import type { PageType } from "@/lib/types";

const TYPE_LABELS: Record<PageType, string> = {
  photo_heavy: "Photo-heavy",
  text_heavy: "Text-heavy",
  chart: "Chart / line art",
  mixed: "Mixed",
};

// complexity_score 高于此值 → "请确认"高亮（分类边界模糊，架构设计 §4.3）
const CONFIRM_THRESHOLD = 0.5;

export default function ReviewGrid({
  taskId,
  onResume,
  resuming,
}: {
  taskId: string;
  onResume: () => void;
  resuming: boolean;
}) {
  const { classifiedPages, overrides, reclassifyPage, toggleOverride } =
    useCompressorStore();
  const [broken, setBroken] = useState<Record<number, boolean>>({});

  const needConfirm = classifiedPages.filter(
    (p) => p.complexity_score >= CONFIRM_THRESHOLD
  ).length;

  return (
    <div data-testid="review-view" className="space-y-6">
      <section className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight">
          Confirm page classification
        </h2>
        <p className="text-sm text-zinc-500">
          Classification determines each page&apos;s compression strategy.
          {needConfirm > 0 && (
            <span className="font-medium text-accent">
              {" "}
              {needConfirm} page{needConfirm > 1 ? "s are" : " is"} uncertain —
              please review {needConfirm > 1 ? "them" : "it"}.
            </span>
          )}
        </p>
        <p className="text-xs text-zinc-500">
          Note: if the target size can&apos;t be reached, we progressively
          rasterize pages (text becomes an image) to hit it, and tell you clearly
          when we do. Pages marked &ldquo;Skip&rdquo; are never touched.
        </p>
      </section>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {classifiedPages.map((page) => {
          const override = overrides[page.page_number];
          const uncertain = page.complexity_score >= CONFIRM_THRESHOLD;
          return (
            <div
              key={page.page_number}
              data-testid={`page-card-${page.page_number}`}
              className={`rounded-lg border bg-white p-3 dark:bg-zinc-800
                ${
                  uncertain
                    ? "border-accent ring-1 ring-accent"
                    : "border-zinc-200 dark:border-zinc-700"
                }`}
            >
              <div className="relative mb-2 aspect-[3/4] overflow-hidden rounded bg-zinc-100 dark:bg-zinc-700">
                {!broken[page.page_number] ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={thumbnailUrl(taskId, page.page_number)}
                    alt={`Page ${page.page_number}`}
                    className="h-full w-full object-contain"
                    loading="lazy"
                    onError={() =>
                      setBroken((b) => ({ ...b, [page.page_number]: true }))
                    }
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-zinc-400">
                    Preview unavailable
                  </div>
                )}
                {uncertain && (
                  <span className="absolute left-1 top-1 rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium text-accent-fg">
                    Review
                  </span>
                )}
                {override === "skip" && (
                  <span className="absolute right-1 top-1 rounded bg-zinc-700 px-1.5 py-0.5 text-[10px] text-white">
                    Skip
                  </span>
                )}
                {override === "aggressive" && (
                  <span className="absolute right-1 top-1 rounded bg-red-600 px-1.5 py-0.5 text-[10px] text-white">
                    Aggressive
                  </span>
                )}
              </div>

              <div className="mb-2 text-xs font-medium">
                Page {page.page_number}
              </div>

              <select
                data-testid={`type-select-${page.page_number}`}
                value={page.page_type}
                disabled={resuming}
                onChange={(e) =>
                  reclassifyPage(page.page_number, e.target.value as PageType)
                }
                className="mb-2 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-600 dark:bg-zinc-700"
              >
                {(Object.keys(TYPE_LABELS) as PageType[]).map((t) => (
                  <option key={t} value={t}>
                    {TYPE_LABELS[t]}
                  </option>
                ))}
              </select>

              <div className="flex gap-1.5">
                <button
                  data-testid={`skip-toggle-${page.page_number}`}
                  disabled={resuming}
                  onClick={() => toggleOverride(page.page_number, "skip")}
                  className={`flex-1 rounded border px-2 py-1 text-[11px] transition
                    ${
                      override === "skip"
                        ? "border-zinc-700 bg-zinc-700 text-white"
                        : "border-zinc-300 dark:border-zinc-600 hover:border-zinc-500"
                    }`}
                >
                  Skip
                </button>
                <button
                  data-testid={`aggressive-toggle-${page.page_number}`}
                  disabled={resuming}
                  onClick={() => toggleOverride(page.page_number, "aggressive")}
                  className={`flex-1 rounded border px-2 py-1 text-[11px] transition
                    ${
                      override === "aggressive"
                        ? "border-red-600 bg-red-600 text-white"
                        : "border-zinc-300 dark:border-zinc-600 hover:border-red-400"
                    }`}
                >
                  Aggressive
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex justify-end border-t border-zinc-200 pt-6 dark:border-zinc-700">
        <button
          data-testid="resume-button"
          onClick={onResume}
          disabled={resuming}
          className="rounded-lg bg-accent px-8 py-3 font-medium text-accent-fg transition hover:bg-accent-hover disabled:opacity-50"
        >
          {resuming ? "Starting…" : "Confirm & continue"}
        </button>
      </div>
    </div>
  );
}
