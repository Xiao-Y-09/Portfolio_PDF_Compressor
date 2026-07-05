/**
 * Phase 14 e2e（手册第六项）：上传 → 等待 review → 修改一页分类 + 标记 skip
 * → 继续 → 下载。全程真实后端（uvicorn + celery worker）。
 * 顺带产出各状态截图到 preview_output/frontend/（用户目视验收物）。
 */

import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

const SHOTS = path.resolve(__dirname, "../../preview_output/frontend");
const BACKEND_PY = path.resolve(__dirname, "../../backend/.venv/Scripts/python.exe");

/** 用后端 venv 的 pymupdf 生成真实小样本（两页：图片+文字+矢量线） */
function makeSamplePdf(): string {
  const out = path.resolve(__dirname, ".sample.pdf");
  const script = `
import pymupdf, random
doc = pymupdf.open()
for i in range(2):
    doc.new_page(width=595, height=842)
    page = doc[i]
    rnd = random.Random(i)
    im = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200))
    im.set_rect(im.irect, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    page.insert_image(pymupdf.Rect(60, 80, 535, 400), pixmap=im)
    for k in range(12):
        page.draw_line((60 + k*40, 460), (80 + k*40, 700), color=(0, 0, 0.8))
    page.insert_text((60, 760), f"E2E sample page {i+1}", fontsize=14)
doc.save(r"${out.replace(/\\/g, "\\\\")}")
`;
  execFileSync(BACKEND_PY, ["-c", script]);
  return out;
}

test("full flow: upload -> review (edit) -> resume -> download", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });
  const pdf = makeSamplePdf();

  // ---- 首页：选目标 + 场景 + 上传 ----
  await page.goto("/");
  await page.getByTestId("target-10").click();
  await page.getByTestId("scenario-screen").click();
  await page.screenshot({ path: path.join(SHOTS, "01-home.png"), fullPage: true });

  await page.getByTestId("file-input").setInputFiles(pdf);

  // ---- review：等待 AWAITING_REVIEW 渲染 ----
  await expect(page.getByTestId("review-view")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("page-card-1")).toBeVisible();
  await expect(page.getByTestId("page-card-2")).toBeVisible();

  // 修改第 1 页分类（手册 e2e 要求"修改一页分类"）+ 第 2 页标记跳过
  await page.getByTestId("type-select-1").selectOption("photo_heavy");
  await page.getByTestId("skip-toggle-2").click();
  await page.screenshot({ path: path.join(SHOTS, "02-review.png"), fullPage: true });

  // ---- 继续压缩 → 后半段任务页 ----
  await page.getByTestId("resume-button").click();

  // ---- SUCCESS：结果与下载 ----
  await expect(page.getByTestId("success-view")).toBeVisible({ timeout: 120_000 });
  await page.screenshot({ path: path.join(SHOTS, "03-success.png"), fullPage: true });

  const downloadHref = await page
    .getByTestId("download-button")
    .getAttribute("href");
  expect(downloadHref).toContain("/api/v1/download/");

  // 下载并校验是 PDF
  const resp = await page.request.get(downloadHref!);
  expect(resp.status()).toBe(200);
  const body = await resp.body();
  expect(body.subarray(0, 5).toString()).toBe("%PDF-");
});

test("failure view renders actionable guidance (session expired)", async ({ page }) => {
  // 直接访问一个不存在的任务 → PENDING 永驻不适合断言；改走 resume 失效路径：
  // 构造 FAILURE 展示——访问任务页并注入一个假的 FAILURE 响应（route mock，
  // 仅测前端渲染层；后端 FAILURE 语义已由 backend test_api 覆盖）
  await page.route("**/api/v1/tasks/fake-failure-task", (route) =>
    route.fulfill({
      json: {
        state: "FAILURE",
        meta: {
          error: {
            phase: "orchestrator",
            code: "CONVERGENCE_FAILED",
            message: "已尝试三种压缩策略仍无法达到 5.0 MB。",
            recoverable: false,
            diagnostics: {
              tier_reached: "full_raster",
              best_achievable_bytes: 12400000,
              skip_page_count: 3,
              skip_raster_bytes: 4000000,
              skip_budget_ratio: 0.87,
            },
          },
        },
      },
    })
  );
  await page.goto("/task/fake-failure-task");
  await expect(page.getByTestId("failure-view")).toBeVisible();
  await expect(page.getByTestId("diagnostics")).toContainText("87%");
  await expect(page.getByTestId("diagnostics")).toContainText("12.4 MB");
  await expect(page.getByTestId("retry-larger-target")).toBeVisible();
  await page.screenshot({
    path: path.join(SHOTS, "04-failure-diagnostics.png"),
    fullPage: true,
  });
});
