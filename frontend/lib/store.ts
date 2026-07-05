/**
 * Zustand store（手册 Phase 14 第三项）。sessionStorage 持久化——
 * review 断点跨页面刷新存活（resume 需要 file_id/target 重建 user_prefs）。
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import type {
  ClassifiedPage,
  CompressionTarget,
  OverrideAction,
  PageOverride,
  PageType,
  TaskState,
} from "./types";

interface CompressorStore {
  // 上传与目标（resume 时重建 user_prefs 必需）
  fileId: string | null;
  sourceSizeMb: number | null;
  targetSizeMb: number;
  compressionTarget: CompressionTarget;
  // 任务
  currentTaskId: string | null;
  taskState: TaskState | null;
  // review：用户可修改的分类副本 + 页级 override
  classifiedPages: ClassifiedPage[];
  overrides: Record<number, OverrideAction | null>;

  setUpload: (fileId: string, sizeMb: number) => void;
  setTarget: (mb: number, target: CompressionTarget) => void;
  setTask: (taskId: string | null, state?: TaskState | null) => void;
  setClassified: (pages: ClassifiedPage[]) => void;
  reclassifyPage: (pageNumber: number, newType: PageType) => void;
  toggleOverride: (pageNumber: number, action: "skip" | "aggressive") => void;
  buildOverrides: () => PageOverride[];
  reset: () => void;
}

/** 目标大小 → 场景预设（《压缩决策引擎.md》§1.3 用户体验层映射；可被用户改选） */
export function presetForTarget(mb: number): CompressionTarget {
  if (mb <= 5) return "email";
  if (mb >= 20) return "print";
  return "screen";
}

export const useCompressorStore = create<CompressorStore>()(
  persist(
    (set, get) => ({
      fileId: null,
      sourceSizeMb: null,
      targetSizeMb: 10,
      compressionTarget: "screen",
      currentTaskId: null,
      taskState: null,
      classifiedPages: [],
      overrides: {},

      setUpload: (fileId, sizeMb) => set({ fileId, sourceSizeMb: sizeMb }),
      setTarget: (mb, target) =>
        set({ targetSizeMb: mb, compressionTarget: target }),
      setTask: (taskId, state = null) =>
        set({ currentTaskId: taskId, taskState: state }),
      setClassified: (pages) => set({ classifiedPages: pages }),

      reclassifyPage: (pageNumber, newType) =>
        set({
          classifiedPages: get().classifiedPages.map((p) =>
            p.page_number === pageNumber ? { ...p, page_type: newType } : p
          ),
        }),

      toggleOverride: (pageNumber, action) =>
        set({
          overrides: {
            ...get().overrides,
            [pageNumber]:
              get().overrides[pageNumber] === action ? null : action,
          },
        }),

      buildOverrides: () => {
        const out: PageOverride[] = [];
        for (const [page, action] of Object.entries(get().overrides)) {
          if (action === "skip" || action === "aggressive")
            out.push({ page_number: Number(page), action });
        }
        return out;
      },

      reset: () =>
        set({
          fileId: null,
          sourceSizeMb: null,
          currentTaskId: null,
          taskState: null,
          classifiedPages: [],
          overrides: {},
        }),
    }),
    {
      name: "pdfc-store",
      storage: createJSONStorage(() => sessionStorage),
    }
  )
);
