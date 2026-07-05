"use client";

/**
 * PROGRESS 视图：进度条 + 阶段文字（关注点 5——百分比由后端按
 * 架构设计 §3.4 分配随 meta 下发，前端只渲染不计算）。
 */

import type { ProgressMeta } from "@/lib/types";

const STAGE_LABELS: Record<string, string> = {
  split: "正在拆页…",
  extract: "正在提取页面元素…",
  classify: "正在分类页面…",
  preprocess: "正在预处理（字体子集化）…",
  converge: "正在压缩收敛…",
  assemble: "正在重组输出…",
};

export default function ProgressView({ meta }: { meta: ProgressMeta }) {
  const percent = Math.round((meta.percent ?? 0.02) * 100);
  const label = STAGE_LABELS[meta.stage ?? ""] ?? "正在处理…";

  return (
    <div data-testid="progress-view" className="mx-auto max-w-xl space-y-4 py-16">
      <div className="flex items-baseline justify-between">
        <span className="font-medium">{label}</span>
        <span className="text-sm tabular-nums text-zinc-500">{percent}%</span>
      </div>
      <div className="h-2.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
        <div
          className="h-full rounded-full bg-accent transition-all duration-700"
          style={{ width: `${Math.max(percent, 2)}%` }}
        />
      </div>
      <p className="text-xs text-zinc-500">
        大文件可能需要几分钟，页面会自动刷新进度。
      </p>
    </div>
  );
}
