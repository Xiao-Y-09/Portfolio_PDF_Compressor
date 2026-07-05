"""Phase 13 端到端测试。

两层：
- 错误路径与边界（恒跑，合成样本，秒级）：加密 PDF / 非 PDF / 损坏 PDF /
  截断 PDF 健壮性探针。
- 真实样本矩阵（3 样本 × 5/10/15/20MB = 12 组，**RUN_E2E=1 显式启用**——
  单组 25~220s，全矩阵 15-30 分钟，不进日常回归；fixtures 缺失时自动跳过）。

矩阵断言（手册 Phase 13 + Tier 系统语义）：
- 零超标：真实产物 gap ≤ +convergence_tolerance/2（铁律 7 的接受带；
  欠冲任意深度合法——§8.4"宁可小一点也不超标"，超出 ±15% 的欠冲仅记录）
- 页数守恒；链接/书签数量不减；单组处理时间 < 300s
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pymupdf
import pytest

from app.config.settings import CompressionConfig, get_settings
from app.contracts import PhaseError, PipelineContext, UserPreferences
from app.pipeline.orchestrator import resume_after_review, run_until_classify
from app.pipeline.split import split_pdf
from app.storage import get_storage

CFG = CompressionConfig()
FIXTURES = Path(__file__).parent / "fixtures" / "portfolios"
SAMPLES = [p.stem for p in sorted(FIXTURES.glob("portfolio_*.pdf"))]
E2E_ENABLED = os.environ.get("RUN_E2E") == "1"

PRESET_FOR_TARGET = {5: "email", 10: "screen", 15: "screen", 20: "print"}


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE__TMP_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ================================================================ 错误路径（恒跑）

def _ctx(tmp_path) -> PipelineContext:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))


def test_encrypted_pdf_rejected_cleanly(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    src = tmp_path / "locked.pdf"
    doc.save(str(src), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="secret", owner_pw="secret")
    doc.close()
    with pytest.raises(PhaseError) as exc_info:
        split_pdf(str(src), _ctx(tmp_path))
    assert exc_info.value.code == "ENCRYPTED_PDF"


def test_non_pdf_rejected_cleanly(tmp_path):
    src = tmp_path / "fake.pdf"
    src.write_bytes(b"GIF89a definitely not a pdf")
    with pytest.raises(PhaseError) as exc_info:
        split_pdf(str(src), _ctx(tmp_path))
    assert exc_info.value.code == "INVALID_PDF"


def test_corrupt_pdf_rejected_cleanly(tmp_path):
    src = tmp_path / "corrupt.pdf"
    src.write_bytes(b"%PDF-1.7 then absolute garbage \x00\x01\x02" * 100)
    with pytest.raises(PhaseError) as exc_info:
        split_pdf(str(src), _ctx(tmp_path))
    assert exc_info.value.code == "INVALID_PDF"


def test_truncated_pdf_no_hang_no_crash(tmp_path):
    """截断 PDF（上传中断的下游形态——Content-Length 不符在 ASGI 层无法用
    测试客户端伪造，真实后果就是落盘字节不完整）：pymupdf 修复模式可能救回
    部分页。断言：不挂起、不崩溃——要么干净的 PhaseError，要么产出有效结果。"""
    from test_assemble import build_pdf_with_images

    full = build_pdf_with_images(tmp_path / "full.pdf", n_pages=3)
    data = full.read_bytes()
    src = tmp_path / "truncated.pdf"
    src.write_bytes(data[: len(data) // 2])  # 拦腰截断

    ctx = _ctx(tmp_path)
    t0 = time.time()
    try:
        raw = split_pdf(str(src), ctx)
        assert 0 < len(raw) <= 3          # 修复模式：页数可少不可多
    except PhaseError as exc:
        assert exc.code == "INVALID_PDF"  # 或干净失败
    assert time.time() - t0 < 30          # 不挂起


# ================================================================ 真实样本矩阵（RUN_E2E=1）

def _run_matrix_scenario(sample: str, target_mb: float, tmp_path):
    src = FIXTURES / f"{sample}.pdf"
    file_id = get_storage().save_upload(src.read_bytes(), "pdf")
    ctx = _ctx(tmp_path)
    prefs = UserPreferences(
        target_size_mb=target_mb,
        compression_target=PRESET_FOR_TARGET[int(target_mb)],
    )
    classified, session_data = run_until_classify(file_id, prefs, ctx)
    out_path = resume_after_review(session_data, classified, prefs)
    return src, Path(out_path), ctx


@pytest.mark.skipif(not E2E_ENABLED, reason="RUN_E2E=1 启用（慢，需真实样本）")
@pytest.mark.skipif(not SAMPLES, reason="fixtures/portfolios 无真实样本")
@pytest.mark.parametrize("target_mb", [5.0, 10.0, 15.0, 20.0])
@pytest.mark.parametrize("sample", SAMPLES)
def test_e2e_matrix(sample, target_mb, tmp_path):
    target_bytes = target_mb * 1024 * 1024
    t0 = time.time()
    src, out, ctx = _run_matrix_scenario(sample, target_mb, tmp_path)
    elapsed = time.time() - t0

    actual = out.stat().st_size
    gap = (actual - target_bytes) / target_bytes
    # 铁律 7：绝不超标超过接受带；欠冲合法（超 -15% 仅打印供观察）
    assert gap <= CFG.convergence_tolerance / 2, f"超标 {gap:+.1%}"
    if gap < -CFG.convergence_tolerance:
        print(f"[undershoot] {sample}@{target_mb}MB: {actual/1e6:.2f}MB ({gap:+.1%})")
    assert elapsed < 300, f"处理超时 {elapsed:.0f}s"

    src_doc = pymupdf.open(str(src))
    out_doc = pymupdf.open(str(out))
    try:
        assert out_doc.page_count == src_doc.page_count          # 页数守恒
        src_links = sum(len(p.get_links()) for p in src_doc)
        out_links = sum(len(p.get_links()) for p in out_doc)
        assert out_links >= src_links                             # 链接不丢
        assert len(out_doc.get_toc()) >= len(src_doc.get_toc())   # 书签不丢
    finally:
        src_doc.close()
        out_doc.close()
    print(f"[e2e] {sample}@{target_mb}MB: {actual/1e6:.2f}MB gap={gap:+.1%} "
          f"tier={ctx.convergence_history[-1].tier} {elapsed:.0f}s")
