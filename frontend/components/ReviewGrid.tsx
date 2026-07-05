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
  photo_heavy: "照片为主",
  text_heavy: "文字为主",
  chart: "图表/线稿",
  mixed: "混合",
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
        <h2 className="text-xl font-semibold">确认页面分类</h2>
        <p className="text-sm text-zinc-500">
          分类影响每页的压缩策略。
          {needConfirm > 0 && (
            <span className="text-accent font-medium">
            　有 {needConfirm} 页分类不确定，请重点确认。
            </span>
          )}
        </p>
        <p className="text-xs text-zinc-500">
          提示：若目标大小无法达成，系统会逐级启用页面光栅化（文字变为图片）
          以保证达标，完成时会明确告知。标记为“跳过”的页面永远不会被改动。
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
                    alt={`第 ${page.page_number} 页`}
                    className="h-full w-full object-contain"
                    loading="lazy"
                    onError={() =>
                      setBroken((b) => ({ ...b, [page.page_number]: true }))
                    }
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-zinc-400">
                    预览不可用
                  </div>
                )}
                {uncertain && (
                  <span className="absolute left-1 top-1 rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium text-white">
                    请确认
                  </span>
                )}
                {override === "skip" && (
                  <span className="absolute right-1 top-1 rounded bg-zinc-700 px-1.5 py-0.5 text-[10px] text-white">
                    跳过
                  </span>
                )}
                {override === "aggressive" && (
                  <span className="absolute right-1 top-1 rounded bg-red-600 px-1.5 py-0.5 text-[10px] text-white">
                    激进
                  </span>
                )}
              </div>

              <div className="mb-2 text-xs font-medium">
                第 {page.page_number} 页
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
                  跳过
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
                  激进
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
          className="rounded-lg bg-accent px-8 py-3 font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
        >
          {resuming ? "正在启动…" : "确认并继续压缩"}
        </button>
      </div>
    </div>
  );
}
