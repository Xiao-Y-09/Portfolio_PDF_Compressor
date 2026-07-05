/**
 * 后端契约的 TS 镜像（Consumer 侧，字段与 backend/app/contracts 一一对应；
 * 铁律 6：本文件只消费不扩展——后端契约变更时同步修改此处）。
 */

export type PageType = "photo_heavy" | "text_heavy" | "chart" | "mixed";
export type CompressionTarget = "screen" | "print" | "email";
export type CompressionTier = "in_place" | "hybrid" | "full_raster";
export type OverrideAction = "skip" | "aggressive" | "reclassify";

export interface ClassifiedPage {
  page_number: number;
  page_type: PageType;
  dominant_element: "raster" | "vector" | "text";
  raster_area_ratio: number;
  vector_area_ratio: number;
  text_area_ratio: number;
  complexity_score: number; // 0=分类明确，1=非常模糊 → 高分高亮"请确认"
}

export interface PageOverride {
  page_number: number;
  action: OverrideAction;
  new_type?: PageType | null;
}

export interface ConvergenceDiagnostics {
  tier_reached: CompressionTier;
  best_achievable_bytes: number;
  skip_page_count: number;
  skip_raster_bytes: number;
  skip_budget_ratio: number; // 可 >1
}

export interface PhaseErrorData {
  phase: string;
  code: string;
  message: string;
  recoverable: boolean;
  diagnostics?: ConvergenceDiagnostics | null;
}

export type TaskState =
  | "PENDING"
  | "PROGRESS"
  | "AWAITING_REVIEW"
  | "SUCCESS"
  | "FAILURE";

export interface ProgressMeta {
  stage?: string;
  percent?: number; // 0-1，后端按架构设计 §3.4 分配
}

export interface AwaitingReviewMeta {
  status: "AWAITING_REVIEW";
  classified: ClassifiedPage[];
  session_id: string;
}

export interface SuccessMeta {
  status: "SUCCESS";
  download_id: string;
  final_size_mb: number;
  tier_used: CompressionTier;
  warning: string | null;
}

export interface FailureMeta {
  error: PhaseErrorData | { code: string; message: string };
}

export interface TaskStatusResponse {
  state: TaskState;
  meta: ProgressMeta | AwaitingReviewMeta | SuccessMeta | FailureMeta | Record<string, never>;
}

/** 前端归一化错误（关注点 3：网络/超时/5xx/业务错误各有文案，绝不露堆栈） */
export interface UiError {
  kind: "network" | "timeout" | "server" | "phase" | "client";
  message: string;
  phase?: PhaseErrorData;
}
