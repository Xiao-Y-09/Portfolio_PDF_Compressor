"use client";

/**
 * FAILURE 视图（关注点 3）：贴心错误展示——CONVERGENCE_FAILED/TARGET_TOO_SMALL
 * 渲染 diagnostics 的可行动建议；各错误码有对应文案；绝不显示堆栈。
 */

import Link from "next/link";

import type { PhaseErrorData, UiError } from "@/lib/types";

const CODE_MESSAGES: Record<string, string> = {
  INVALID_PDF: "文件无法识别为有效的 PDF，请检查文件是否损坏后重新上传。",
  ENCRYPTED_PDF: "PDF 已加密，请先解除密码保护后重新上传。",
  SESSION_EXPIRED: "确认会话已过期（超过 30 分钟未操作），请重新上传文件。",
  TARGET_TOO_SMALL: "目标大小对这份文件来说过小。",
  CONVERGENCE_FAILED: "已尝试全部压缩策略，仍无法达到目标大小。",
  EXECUTION_FAILURE_RATE: "压缩过程中失败率过高，请重试；若持续失败请更换文件。",
  ASSEMBLY_FAILED: "输出文件生成失败，请重试。",
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
          您跳过了 <b>{diag.skip_page_count}</b> 页，占目标预算的{" "}
          <b>{Math.round(diag.skip_budget_ratio * 100)}%</b>
          ——减少跳过页数可显著释放空间。
        </li>
      )}
      <li>
        当前预估最优可达{" "}
        <b>{(diag.best_achievable_bytes / 1e6).toFixed(1)} MB</b>
        ——选择不小于该值的目标可一次成功。
      </li>
    </ul>
  );
}

export default function ErrorView({ error }: { error: UiError }) {
  const phase = error.phase;
  const headline = phase
    ? CODE_MESSAGES[phase.code] ?? "处理失败，请重试。"
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
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
                >
                  选择更大目标重试
                </Link>
              </div>
            </div>
          )}
        {(!phase ||
          !["CONVERGENCE_FAILED", "TARGET_TOO_SMALL"].includes(phase.code)) && (
          <Link
            href="/"
            className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
          >
            返回重新上传
          </Link>
        )}
      </div>
    </div>
  );
}
