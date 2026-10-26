"""Bounded text extraction for downloaded documents (PROJECT_PLAN Step 6).

- PDFs are parsed with ``pypdf`` in a spawned child process that is killed when
  ``document_time_budget_seconds`` runs out; at most ``max_pages`` pages are read.
- A PDF with no text layer is OCRed with ``pdftoppm`` + ``tesseract`` when both
  binaries exist, on the first ``ocr_max_pages`` pages within the remaining
  budget. Otherwise it is ``SCANNED`` with the warning ``ocr_unavailable``.
- HTML is reduced to visible text with BeautifulSoup.
- Other types (XLSX, DOCX, legacy Office, unknown) are ``UNSUPPORTED``: hash only.

Text lives only in memory. OCR alone writes the PDF and page images, and only
inside a ``TemporaryDirectory`` that is removed before returning.
"""

from __future__ import annotations

import io
import multiprocessing
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policy_tracker.documents import signatures
from policy_tracker.documents.language import HINDI, detect_language
from policy_tracker.documents.types import EnrichmentLimits, ExtractedText, ExtractionStatus

MAX_TEXT_CHARS = 2_000_000  # bound on the in-memory text per document
CHILD_MEMORY_BYTES = 2 * 1024**3  # address-space cap for the PDF child, where supported
OCR_RESOLUTION_DPI = 200

PdfTextFn = Callable[[bytes, int], tuple[list[str], int]]
"""``(pdf_bytes, max_pages) -> (page_texts, total_pages)``; must be picklable."""


def normalize_text(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""

    return " ".join(text.split())


def has_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)


def pypdf_text(data: bytes, max_pages: int) -> tuple[list[str], int]:
    """Extract the text of the first ``max_pages`` pages with pypdf (runs in the child)."""

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    texts = [reader.pages[index].extract_text() or "" for index in range(min(total, max_pages))]
    return texts, total


def _limit_child_memory() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def _pdf_child(conn: Any, fn: PdfTextFn, data: bytes, max_pages: int) -> None:
    _limit_child_memory()
    try:
        conn.send(("ok", fn(data, max_pages)))
    except BaseException as exc:  # noqa: BLE001 - report every parser failure to the parent
        conn.send(("error", type(exc).__name__))
    finally:
        conn.close()


def run_bounded(fn: PdfTextFn, data: bytes, max_pages: int, timeout: float) -> tuple[str, Any]:
    """Run ``fn`` in a spawned process; return ``("ok", result)``, ``("timeout", None)``,
    or ``("error", exception_name)``. The child is killed on timeout."""

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_child, args=(child, fn, data, max_pages), daemon=True)
    process.start()
    child.close()
    try:
        if parent.poll(max(timeout, 0.0)):
            try:
                outcome: tuple[str, Any] = parent.recv()
            except EOFError:
                outcome = ("error", "child_exited")
        else:
            outcome = ("timeout", None)
    finally:
        parent.close()
        process.join(1.0)
        if process.is_alive():
            process.kill()
            process.join()
    return outcome


def _page_number(path: Path) -> int:
    digits = re.findall(r"\d+", path.stem)
    return int(digits[-1]) if digits else 0


@dataclass(frozen=True)
class Extractor:
    """Extraction strategy; every collaborator is injectable for deterministic tests."""

    pdf_text: PdfTextFn = pypdf_text
    which: Callable[[str], str | None] = shutil.which
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run
    clock: Callable[[], float] = time.monotonic

    def extract(
        self, document_key: str, body: bytes, detected: str, limits: EnrichmentLimits
    ) -> ExtractedText:
        """Extract text from verified bytes of type ``detected`` (see ``signatures``)."""

        if detected == signatures.PDF:
            return self._pdf(document_key, body, limits)
        if detected == signatures.HTML:
            return self._html(document_key, body)
        return ExtractedText(
            document_key, ExtractionStatus.UNSUPPORTED, warnings=(f"unsupported_type:{detected}",)
        )

    # -- HTML ---------------------------------------------------------------

    def _html(self, key: str, body: bytes) -> ExtractedText:
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(body, "lxml")
            for node in soup(["script", "style", "noscript", "template"]):
                node.decompose()
            text = soup.get_text("\n")
        except Exception as exc:  # noqa: BLE001 - untrusted markup never aborts the run
            return ExtractedText(key, ExtractionStatus.FAILED, warnings=(f"html_error:{exc!r}",))
        lines = [normalize_text(line) for line in text.splitlines()]
        return _finish(key, "\n".join(line for line in lines if line), ExtractionStatus.OK)

    # -- PDF ----------------------------------------------------------------

    def _pdf(self, key: str, body: bytes, limits: EnrichmentLimits) -> ExtractedText:
        budget = limits.document_time_budget_seconds
        deadline = self.clock() + budget
        outcome, payload = run_bounded(self.pdf_text, body, limits.max_pages, budget)
        if outcome == "timeout":
            return ExtractedText(key, ExtractionStatus.FAILED, warnings=("pdf_time_budget",))
        if outcome != "ok":
            return ExtractedText(key, ExtractionStatus.FAILED, warnings=(f"pdf_error:{payload}",))
        page_texts, total = payload
        warnings: list[str] = []
        if total > limits.max_pages:
            warnings.append(f"page_cap:{limits.max_pages}/{total}")
        text = "\n".join(page_texts)
        if has_letters(text):
            status = ExtractionStatus.PARTIAL if total > limits.max_pages else ExtractionStatus.OK
            return _finish(key, text, status, pages=total, warnings=warnings)
        warnings.append("no_text_layer")
        ocr_pages = min(total, limits.ocr_max_pages)
        ocr_text, ocr_warnings = self._ocr(body, ocr_pages, deadline - self.clock())
        warnings.extend(ocr_warnings)
        if ocr_text is not None and has_letters(ocr_text):
            if total > ocr_pages:
                warnings.append(f"ocr_page_cap:{ocr_pages}/{total}")
            return _finish(key, ocr_text, ExtractionStatus.OCR, pages=total, warnings=warnings)
        if ocr_text is not None:
            warnings.append("ocr_no_text")
        return ExtractedText(key, ExtractionStatus.SCANNED, pages=total, warnings=tuple(warnings))

    def _ocr(self, body: bytes, pages: int, remaining: float) -> tuple[str | None, list[str]]:
        """OCR the first ``pages`` pages; ``None`` text means OCR could not run at all."""

        pdftoppm, tesseract = self.which("pdftoppm"), self.which("tesseract")
        if not pdftoppm or not tesseract:
            return None, ["ocr_unavailable"]
        if pages <= 0 or remaining <= 0:
            return None, ["ocr_time_budget"]
        deadline = self.clock() + remaining
        with tempfile.TemporaryDirectory(prefix="policy-tracker-ocr-") as tmp:
            source = Path(tmp) / "document.pdf"
            source.write_bytes(body)
            render = [pdftoppm, "-r", str(OCR_RESOLUTION_DPI), "-gray", "-png", "-f", "1"]
            render += ["-l", str(pages), str(source), str(Path(tmp) / "page")]
            try:
                self.run(render, capture_output=True, timeout=remaining, check=True)
            except subprocess.TimeoutExpired:
                return None, ["ocr_time_budget"]
            except (subprocess.CalledProcessError, OSError):
                return None, ["ocr_failed:render"]
            languages = self._languages(tesseract, deadline)
            texts: list[str] = []
            warnings: list[str] = []
            for image in sorted(Path(tmp).glob("page*.png"), key=_page_number):
                left = deadline - self.clock()
                if left <= 0:
                    warnings.append("ocr_time_budget")
                    break
                command = [tesseract, str(image), "stdout"]
                if languages:
                    command += ["-l", languages]
                try:
                    done = self.run(command, capture_output=True, timeout=left, check=True)
                except subprocess.TimeoutExpired:
                    warnings.append("ocr_time_budget")
                    break
                except (subprocess.CalledProcessError, OSError):
                    warnings.append(f"ocr_failed:page_{_page_number(image)}")
                    continue
                texts.append(bytes(done.stdout or b"").decode("utf-8", "replace"))
        return "\n".join(texts), warnings

    def _languages(self, tesseract: str, deadline: float) -> str | None:
        try:
            done = self.run(
                [tesseract, "--list-langs"],
                capture_output=True,
                timeout=max(deadline - self.clock(), 0.1),
                check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        output = bytes(done.stdout or b"").decode("utf-8", "replace").split()
        wanted = [lang for lang in ("eng", "hin") if lang in output]
        return "+".join(wanted) or None


def _finish(
    key: str,
    text: str,
    status: ExtractionStatus,
    *,
    pages: int | None = None,
    warnings: Sequence[str] = (),
) -> ExtractedText:
    """Bound the text, detect its language, and mark Hindi-only documents."""

    notes = list(warnings)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        notes.append(f"text_truncated:{MAX_TEXT_CHARS}")
    language = detect_language(text)
    if language == HINDI:
        notes.append(f"hindi_only_from:{status.value}")
        status = ExtractionStatus.HINDI_ONLY
    return ExtractedText(key, status, text, language, pages, tuple(notes))
