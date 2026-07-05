"use client";

/**
 * SUCCESS 视图：最终大小 + 压缩率 + 下载；tier_used/warning 展示（关注点 4）
 * ——in_place 无警告，hybrid/full_raster 显示降级说明 + "重试更大目标"入口。
 */

import Link from "next/link";

import { downloadUrl } from "@/lib/api";
import type { CompressionTier, SuccessMeta } from "@/lib/types";

const TIER_NOTES: Record<
  CompressionTier,
  { label: string; message: string | null }
> = {
  in_place: { label: "保真压缩", message: null },
  hybrid: {
    label: "混合压缩",
    message:
      "部分复杂页面已被光栅化（矢量与文字转为图片）以达到目标大小，其余页面保持原始质量。",
  },
  full_raster: {
    label: "极限压缩",
    message: "已使用极限压缩，全部页面已光栅化，部分文字可能模糊。",
  },
};

export default function ResultView({
  meta,
  sourceSizeMb,
}: {
  meta: SuccessMeta;
  sourceSizeMb: number | null;
}) {
  const ratio =
    sourceSizeMb && sourceSizeMb > 0
      ? Math.round((1 - meta.final_size_mb / sourceSizeMb) * 100)
      : null;
  const note = TIER_NOTES[meta.tier_used] ?? TIER_NOTES.in_place;
  const degraded = meta.tier_used !== "in_place";

  return (
    <div data-testid="success-view" className="mx-auto max-w-xl space-y-6 py-12">
      <div className="rounded-xl border border-zinc-200 bg-white p-8 text-center dark:border-zinc-700 dark:bg-zinc-800">
        <p className="text-sm text-zinc-500">压缩完成 · {note.label}</p>
        <p className="mt-3 text-4xl font-semibold tabular-nums">
          {meta.final_size_mb.toFixed(2)} MB
        </p>
        {ratio !== null && (
          <p className="mt-1 text-sm text-zinc-500">
            {sourceSizeMb!.toFixed(1)} MB → 体积减少 {ratio}%
          </p>
        )}
        <a
          data-testid="download-button"
          href={downloadUrl(meta.download_id)}
          className="mt-6 inline-block rounded-lg bg-accent px-8 py-3 font-medium text-white transition hover:bg-accent-hover"
        >
          下载 PDF
        </a>
        <p className="mt-3 text-xs text-zinc-400">下载链接 5 分钟内有效</p>
      </div>

      {degraded && (
        <div
          data-testid="tier-warning"
          className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-700 dark:bg-amber-950"
        >
          <p className="font-medium text-amber-800 dark:text-amber-300">
            {meta.warning ?? note.message}
          </p>
          <Link
            data-testid="retry-larger"
            href="/"
            className="mt-2 inline-block text-sm font-medium text-accent underline"
          >
            重试更大目标以获得更好质量 →
          </Link>
        </div>
      )}

      <div className="text-center">
        <Link href="/" className="text-sm text-zinc-500 underline">
          压缩另一个文件
        </Link>
      </div>
    </div>
  );
}
