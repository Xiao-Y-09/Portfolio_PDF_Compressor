/**
 * API 客户端（手册 Phase 14 第四项）。业务逻辑全部在后端——本层只有
 * HTTP 调用与错误归一化，不做任何压缩决策（关注点 1）。
 */

import axios, { AxiosError } from "axios";

import { getClientSession } from "./session";
import type {
  ClassifiedPage,
  CompressionTarget,
  PageOverride,
  PhaseErrorData,
  TaskStatusResponse,
  UiError,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const http = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 60_000, // 上传大文件也要留足；轮询单独更短
});

http.interceptors.request.use((config) => {
  config.headers["X-Client-Session"] = getClientSession();
  return config;
});

/** 关注点 3：错误归一化——网络/超时/5xx/业务错误分流，绝不透出堆栈 */
export function normalizeError(err: unknown): UiError {
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<{ error?: PhaseErrorData; detail?: string }>;
    if (ax.code === "ECONNABORTED")
      return {
        kind: "timeout",
        message: "The request timed out. Check your connection and try again.",
      };
    if (!ax.response)
      return {
        kind: "network",
        message: "Couldn't reach the server. Please try again shortly.",
      };
    const body = ax.response.data;
    if (body && typeof body === "object" && "error" in body && body.error) {
      const phase = body.error as PhaseErrorData;
      return { kind: "phase", message: phase.message, phase };
    }
    if (ax.response.status >= 500)
      return {
        kind: "server",
        message: "The service is temporarily unavailable. Please try again shortly.",
      };
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail?: string }).detail)
        : `Request failed (${ax.response.status})`;
    return { kind: "client", message: detail };
  }
  return {
    kind: "client",
    message: "An unexpected error occurred. Please try again.",
  };
}

export async function uploadPdf(
  file: File
): Promise<{ file_id: string; size_mb: number }> {
  const form = new FormData();
  form.append("file", file);
  const resp = await http.post("/upload", form, { timeout: 300_000 });
  return resp.data;
}

export async function startCompression(
  fileId: string,
  targetSizeMb: number,
  compressionTarget: CompressionTarget,
  perPageOverrides: PageOverride[] = []
): Promise<{ task_id: string }> {
  const resp = await http.post("/compress", {
    file_id: fileId,
    target_size_mb: targetSizeMb,
    compression_target: compressionTarget,
    per_page_overrides: perPageOverrides,
  });
  return resp.data;
}

export async function getTaskStatus(
  taskId: string
): Promise<TaskStatusResponse> {
  const resp = await http.get(`/tasks/${taskId}`, { timeout: 10_000 });
  return resp.data;
}

export async function resumeTask(
  taskId: string,
  sessionId: string,
  modifiedClassified: ClassifiedPage[],
  userPrefs: {
    file_id: string;
    target_size_mb: number;
    compression_target: CompressionTarget;
    per_page_overrides: PageOverride[];
  }
): Promise<{ task_id: string }> {
  const resp = await http.post(`/tasks/${taskId}/resume`, {
    session_id: sessionId,
    modified_classified: modifiedClassified,
    user_prefs: userPrefs,
  });
  return resp.data;
}

export function downloadUrl(downloadId: string): string {
  return `${API_BASE}/api/v1/download/${downloadId}`;
}

export function thumbnailUrl(taskId: string, pageNumber: number): string {
  return `${API_BASE}/api/v1/tasks/${taskId}/pages/${pageNumber}/thumbnail`;
}
