"""Phase 4 拆页测试。

fixture 说明：拆页是结构级操作，测试 PDF 由 pymupdf 程序化生成（真实 PDF 结构、
真实 AES-256 加密），满足手册 Phase 4 的"5 页测试 PDF"要求；元素级的真实作品集
样本（规则 6）从 Phase 5 起使用 tests/fixtures/portfolios/ 下的用户提供文件。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pymupdf
import pytest

from app.contracts import PhaseError, PipelineContext
from app.pipeline.split import split_pdf

A4_W, A4_H = 595.0, 842.0


def make_pdf(path: Path, pages: int = 5) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=A4_W, height=A4_H)
        page.insert_text((72, 72), f"Page {i + 1} of test portfolio")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def ctx(tmp_path) -> PipelineContext:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(workspace))


# ---------------------------------------------------------------- 正常路径

def test_split_five_pages_returns_five_rawpages(tmp_path, ctx):
    pdf = make_pdf(tmp_path / "five.pdf", pages=5)
    pages = split_pdf(str(pdf), ctx)

    assert len(pages) == 5
    assert [p.page_number for p in pages] == [1, 2, 3, 4, 5]  # 页号从 1 开始
    for p in pages:
        assert p.page_width == pytest.approx(A4_W)
        assert p.page_height == pytest.approx(A4_H)
        # §1.6：POSIX 相对路径，正斜杠，无盘符
        assert p.page_bytes_ref == f"pages/page_{p.page_number}.pdf"
        assert "\\" not in p.page_bytes_ref


def test_page_refs_exist_and_are_reopenable(tmp_path, ctx):
    pdf = make_pdf(tmp_path / "three.pdf", pages=3)
    pages = split_pdf(str(pdf), ctx)

    for p in pages:
        saved = ctx.resolve_ref(p.page_bytes_ref)  # 唯一合法解析入口
        assert saved.is_file() and saved.stat().st_size > 0
        single = pymupdf.open(str(saved))
        try:
            assert single.is_pdf
            assert single.page_count == 1  # 每个 ref 是真实的单页 PDF
        finally:
            single.close()


def test_split_logs_page_bytes_at_info(tmp_path, ctx, caplog):
    """关注点 1/4：每页字节数记录进 app.pipeline.split 的 INFO 日志（观测用）。"""
    pdf = make_pdf(tmp_path / "two.pdf", pages=2)
    with caplog.at_level(logging.INFO, logger="app.pipeline.split"):
        split_pdf(str(pdf), ctx)
    records = [r for r in caplog.records if r.name == "app.pipeline.split"]
    assert len(records) == 2
    assert all("bytes" in r.getMessage() for r in records)


# ---------------------------------------------------------------- 错误路径

def test_encrypted_pdf_raises_encrypted_phase_error(tmp_path, ctx):
    """关注点 3：真实 AES-256 加密 PDF（非 mock）。"""
    plain = make_pdf(tmp_path / "plain.pdf", pages=2)
    doc = pymupdf.open(str(plain))
    encrypted = tmp_path / "encrypted.pdf"
    doc.save(
        str(encrypted),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    doc.close()

    # 前置自证：这确实是一个需要密码的 PDF
    locked = pymupdf.open(str(encrypted))
    assert locked.needs_pass
    locked.close()

    with pytest.raises(PhaseError) as excinfo:
        split_pdf(str(encrypted), ctx)
    err = excinfo.value
    assert err.phase == "split"
    assert err.code == "ENCRYPTED_PDF"
    assert err.recoverable is False


def test_non_pdf_raises_invalid_phase_error(tmp_path, ctx):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"this is definitely not a pdf \x00\x01\x02")
    with pytest.raises(PhaseError) as excinfo:
        split_pdf(str(fake), ctx)
    assert excinfo.value.phase == "split"
    assert excinfo.value.code == "INVALID_PDF"


def test_single_page_failure_warns_and_continues(tmp_path, ctx, caplog, monkeypatch):
    """单页失败不阻塞（故障注入：第 3 页的单页文档创建抛错）。"""
    pdf = make_pdf(tmp_path / "five.pdf", pages=5)

    real_open = pymupdf.open
    state = {"per_page_calls": 0}

    def flaky_open(*args, **kwargs):
        if not args and not kwargs:  # 无参调用 = split 内部为每页新建的临时文档
            state["per_page_calls"] += 1
            if state["per_page_calls"] == 3:
                raise RuntimeError("injected failure on page 3")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", flaky_open)

    with caplog.at_level(logging.WARNING, logger="app.pipeline.split"):
        pages = split_pdf(str(pdf), ctx)

    assert len(pages) == 4  # 数量减少
    assert [p.page_number for p in pages] == [1, 2, 4, 5]  # 其余页不受影响
    warned = [r for r in caplog.records
              if r.name == "app.pipeline.split" and r.levelno == logging.WARNING]
    assert len(warned) == 1
    assert "page 3" in warned[0].getMessage()
