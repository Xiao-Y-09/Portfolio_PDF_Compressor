"use client";

/**
 * /task/[taskId]（手册 Phase 14 第二项）：按任务状态渲染四种视图，
 * 每 2 秒轮询 GET /api/v1/tasks/:taskId（终态停止轮询）。
 * resume 后跳到后半段新 task 页继续轮询。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import ErrorView from "@/components/ErrorView";
import ProgressView from "@/components/ProgressView";
import ResultView from "@/components/ResultView";
import ReviewGrid from "@/components/ReviewGrid";
import { getTaskStatus, normalizeError, resumeTask } from "@/lib/api";
import { useCompressorStore } from "@/lib/store";
import type {
  AwaitingReviewMeta,
  FailureMeta,
  PhaseErrorData,
  ProgressMeta,
  SuccessMeta,
  TaskStatusResponse,
  UiError,
} from "@/lib/types";

const POLL_INTERVAL_MS = 2000; // 手册 §4.3：前端每 2 秒轮询

export default function TaskPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const router = useRouter();
  const store = useCompressorStore();

  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [uiError, setUiError] = useState<UiError | null>(null);
  const [resuming, setResuming] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const pollErrors = useRef(0);

  // ---- 轮询 ----
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;
      try {
        const s = await getTaskStatus(taskId);
        pollErrors.current = 0;
        setStatus(s);
        store.setTask(taskId, s.state);
        if (s.state === "AWAITING_REVIEW") {
          const meta = s.meta as AwaitingReviewMeta;
          sessionIdRef.current = meta.session_id;
          // 只在首次进入 review 时填充可编辑副本（保留用户已做的修改）
          if (store.classifiedPages.length !== meta.classified.length) {
            store.setClassified(meta.classified);
          }
        }
        if (s.state === "SUCCESS" || s.state === "FAILURE") return; // 终态停止
      } catch (err) {
        // 轮询瞬断容忍：连续 5 次失败才升级为页面错误
        pollErrors.current += 1;
        if (pollErrors.current >= 5) {
          setUiError(normalizeError(err));
          return;
        }
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // ---- resume ----
  const onResume = useCallback(async () => {
    if (!sessionIdRef.current || !store.fileId) {
      setUiError({
        kind: "client",
        message:
          "Session information is missing (it may have been lost on page refresh). Please upload the file again.",
      });
      return;
    }
    setResuming(true);
    try {
      const { task_id } = await resumeTask(
        taskId,
        sessionIdRef.current,
        store.classifiedPages,
        {
          file_id: store.fileId,
          target_size_mb: store.targetSizeMb,
          compression_target: store.compressionTarget,
          per_page_overrides: store.buildOverrides(),
        }
      );
      store.setTask(task_id, "PENDING");
      setStatus(null);
      router.push(`/task/${task_id}`);
    } catch (err) {
      setUiError(normalizeError(err));
    } finally {
      setResuming(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, router]);

  // ---- 渲染 ----
  if (uiError) return <ErrorView error={uiError} />;
  if (!status)
    return <ProgressView meta={{ stage: undefined, percent: 0.02 }} />;

  switch (status.state) {
    case "AWAITING_REVIEW":
      return (
        <ReviewGrid taskId={taskId} onResume={onResume} resuming={resuming} />
      );
    case "SUCCESS":
      return (
        <ResultView
          meta={status.meta as SuccessMeta}
          sourceSizeMb={store.sourceSizeMb}
        />
      );
    case "FAILURE": {
      const meta = status.meta as FailureMeta;
      const phase = meta.error as PhaseErrorData;
      return (
        <ErrorView
          error={{
            kind: "phase",
            message: phase.message ?? "Processing failed",
            phase: phase.code ? phase : undefined,
          }}
        />
      );
    }
    default:
      return <ProgressView meta={status.meta as ProgressMeta} />;
  }
}
