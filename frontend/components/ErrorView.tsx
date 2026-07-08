"use client";

/**
 * FAILURE 视图（关注点 3）：贴心错误展示——CONVERGENCE_FAILED/TARGET_TOO_SMALL
 * 渲染 diagnostics 的可行动建议；各错误码有对应文案；绝不显示堆栈。
 */

import Link from "next/link";

import type { PhaseErrorData, UiError } from "@/lib/types";

const CODE_MESSAGES: Record<string, string> = {
  INVALID_PDF:
    "This file couldn't be recognized as a valid PDF. Check that it isn't corrupted and upload it again.",
  ENCRYPTED_PDF:
    "This PDF is password-protected. Remove the protection and upload it again.",
  SESSION_EXPIRED:
    "Your review session expired (no activity for over 30 minutes). Please upload the file again.",
  TARGET_TOO_SMALL: "The target size is too small for this file.",
  CONVERGENCE_FAILED:
    "We tried every compression strategy but still couldn't reach the target size.",
  EXECUTION_FAILURE_RATE:
    "Too many pages failed during compression. Please retry, or try a different file if it keeps failing.",
  ASSEMBLY_FAILED: "The output file couldn't be generated. Please try again.",
};

function ActionableSuggestions({ error }: { error: PhaseErrorData }) {
  const diag = error.diagnostics;
  if (!diag) return null;
  return (
    <ul
      data-testid="diagnostics"
      className="mt-3 list-disc space-y-1.5 pl-5 text-sm"
    >
      {diag.skip_page_count > 0 && (
        <li>
          You skipped <b>{diag.skip_page_count}</b> page
          {diag.skip_page_count > 1 ? "s" : ""}, taking up{" "}
          <b>{Math.round(diag.skip_budget_ratio * 100)}%</b> of the target budget
          — skipping fewer pages frees up significant space.
        </li>
      )}
      <li>
        The best achievable size is currently about{" "}
        <b>{(diag.best_achievable_bytes / 1e6).toFixed(1)} MB</b> — choosing a
        target no smaller than that will succeed on the first try.
      </li>
    </ul>
  );
}

export default function ErrorView({ error }: { error: UiError }) {
  const phase = error.phase;
  const headline = phase
    ? CODE_MESSAGES[phase.code] ?? "Something went wrong. Please try again."
    : error.message;

  return (
    <div data-testid="failure-view" className="mx-auto max-w-xl py-12">
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950">
        <h2 className="font-semibold text-red-800 dark:text-red-300">
          {headline}
        </h2>
        {phase &&
          (phase.code === "CONVERGENCE_FAILED" ||
            phase.code === "TARGET_TOO_SMALL") && (
            <div className="mt-2 text-red-700 dark:text-red-300">
              <ActionableSuggestions error={phase} />
              <div className="mt-4 flex gap-3">
                <Link
                  data-testid="retry-larger-target"
                  href="/"
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover"
                >
                  Retry with a larger target
                </Link>
              </div>
            </div>
          )}
        {(!phase ||
          !["CONVERGENCE_FAILED", "TARGET_TOO_SMALL"].includes(phase.code)) && (
          <Link
            href="/"
            className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover"
          >
            Back to upload
          </Link>
        )}
      </div>
    </div>
  );
}
