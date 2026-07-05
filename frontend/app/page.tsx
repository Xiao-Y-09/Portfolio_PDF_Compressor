"use client";

/**
 * 首页（手册 Phase 14 第一项）：上传区 + 目标大小 + 压缩场景 → /task/[taskId]。
 * 前端不做任何压缩决策（关注点 1）——目标与场景只是透传给后端的用户偏好。
 */

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { useDropzone } from "react-dropzone";

import { normalizeError, startCompression, uploadPdf } from "@/lib/api";
import { presetForTarget, useCompressorStore } from "@/lib/store";
import type { CompressionTarget, UiError } from "@/lib/types";

const TARGETS = [5, 10, 15, 20] as const;
const SCENARIOS: { value: CompressionTarget; label: string; hint: string }[] = [
  { value: "screen", label: "屏幕查看", hint: "日常投递，平衡质量与体积" },
  { value: "print", label: "打印", hint: "最保守，优先清晰度" },
  { value: "email", label: "邮件附件", hint: "极限压缩，体积优先" },
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
        setBusy("正在上传…");
        const { file_id, size_mb } = await uploadPdf(file);
        store.setUpload(file_id, size_mb);
        setBusy("正在启动压缩…");
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
    <div className="space-y-8">
      <section className="space-y-2">
        <h2 className="text-2xl font-semibold">压缩你的作品集</h2>
        <p className="text-zinc-500 dark:text-zinc-400">
          选择目标大小与场景，上传 PDF——文字、矢量、书签与超链接将被结构化保留。
        </p>
      </section>

      {/* 目标大小 */}
      <section>
        <h3 className="mb-3 text-sm font-medium text-zinc-500">目标大小</h3>
        <div className="flex gap-3">
          {TARGETS.map((mb) => (
            <button
              key={mb}
              data-testid={`target-${mb}`}
              onClick={() => store.setTarget(mb, presetForTarget(mb))}
              className={`rounded-lg border px-5 py-2.5 text-sm font-medium transition
                ${
                  store.targetSizeMb === mb
                    ? "border-accent bg-accent text-white"
                    : "border-zinc-300 dark:border-zinc-600 hover:border-accent"
                }`}
            >
              {mb} MB
            </button>
          ))}
        </div>
      </section>

      {/* 压缩场景 */}
      <section>
        <h3 className="mb-3 text-sm font-medium text-zinc-500">压缩场景</h3>
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
                    : "border-zinc-300 dark:border-zinc-600 hover:border-accent"
                }`}
            >
              <div className="font-medium">{s.label}</div>
              <div className="mt-1 text-xs text-zinc-500">{s.hint}</div>
            </button>
          ))}
        </div>
      </section>

      {/* 上传区 */}
      <section
        {...getRootProps()}
        data-testid="dropzone"
        className={`flex min-h-48 cursor-pointer flex-col items-center justify-center
          rounded-xl border-2 border-dashed p-8 text-center transition
          ${
            isDragActive
              ? "border-accent bg-orange-50 dark:bg-zinc-800"
              : "border-zinc-300 dark:border-zinc-600 hover:border-accent"
          }`}
      >
        <input {...getInputProps()} data-testid="file-input" />
        {busy ? (
          <p className="text-accent font-medium animate-pulse">{busy}</p>
        ) : (
          <>
            <p className="font-medium">拖入 PDF，或点击选择文件</p>
            <p className="mt-2 text-sm text-zinc-500">最大 500MB</p>
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
    </div>
  );
}
