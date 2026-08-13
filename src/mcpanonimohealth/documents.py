"""Leitura e OCR locais de documentos; nenhum original é persistido pelo projeto."""

from __future__ import annotations

import re
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 10
MIN_MEDIAN_CONFIDENCE = 0.90
MIN_LINE_CONFIDENCE = 0.75
SUPPORTED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".txt",
}


class DocumentError(Exception):
    """Falha segura de entrada, sem incluir nome ou caminho do arquivo."""


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    index: int
    text: str
    confidences: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    pages: tuple[ExtractedPage, ...]
    text: str
    hold_reasons: tuple[str, ...]
    median_confidence: float
    duration_ms: int


_ocr_instance = None
_ocr_lock = threading.Lock()


def _ocr():
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                from rapidocr import RapidOCR

                _ocr_instance = RapidOCR()
    return _ocr_instance


def _register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        return


def _contains_qr(image: Image.Image) -> bool:
    try:
        import cv2
        import numpy as np

        array = np.asarray(image.convert("RGB"))[:, :, ::-1]
        qr_detector = cv2.QRCodeDetector()
        _value, points, _straight = qr_detector.detectAndDecode(array)
        if points is not None:
            return True
        barcode_detector = getattr(cv2, "barcode_BarcodeDetector", None)
        if barcode_detector is None:
            return False
        detected, _points = barcode_detector().detect(array)
        return bool(detected)
    except Exception:
        return False


def _ocr_page(image: Image.Image, index: int) -> tuple[ExtractedPage, list[str]]:
    image = ImageOps.exif_transpose(image).convert("RGB")
    reasons: list[str] = []
    if _contains_qr(image):
        reasons.append(f"PAGE_{index}_QR_OR_BARCODE")
    try:
        output = _ocr()(image)
        texts = tuple(str(value).strip() for value in (output.txts or ()) if str(value).strip())
        scores = tuple(float(value) for value in (output.scores or ()))
    except Exception as exc:
        raise DocumentError("OCR_FAILED") from exc
    if not texts or not scores:
        reasons.append(f"PAGE_{index}_NO_TEXT")
        return ExtractedPage(index, "", ()), reasons
    if min(scores) < MIN_LINE_CONFIDENCE:
        reasons.append(f"PAGE_{index}_LOW_CONFIDENCE")
    return ExtractedPage(index, "\n".join(texts), scores), reasons


def _pdf_pages(path: Path) -> tuple[list[ExtractedPage], list[str]]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:
        raise DocumentError("PDF_CORRUPT_OR_PROTECTED") from exc
    if len(document) > MAX_PAGES:
        raise DocumentError("TOO_MANY_PAGES")
    pages: list[ExtractedPage] = []
    reasons: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=200 / 72)
            extracted, page_reasons = _ocr_page(bitmap.to_pil(), index + 1)
            pages.append(extracted)
            reasons.extend(page_reasons)
    finally:
        document.close()
    return pages, reasons


def _image_pages(path: Path) -> tuple[list[ExtractedPage], list[str]]:
    _register_heif()
    pages: list[ExtractedPage] = []
    reasons: list[str] = []
    try:
        with Image.open(path) as image:
            frames = int(getattr(image, "n_frames", 1))
            if frames > MAX_PAGES:
                raise DocumentError("TOO_MANY_PAGES")
            for index in range(frames):
                image.seek(index)
                extracted, page_reasons = _ocr_page(image.copy(), index + 1)
                pages.append(extracted)
                reasons.extend(page_reasons)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("IMAGE_CORRUPT_OR_UNSUPPORTED") from exc
    return pages, reasons


def extract_document(path: Path) -> ExtractedDocument:
    """Extrai texto e destrói toda referência pública ao caminho ao retornar."""

    import time

    started = time.monotonic()
    if path.is_symlink() or not path.is_file():
        raise DocumentError("INVALID_SELECTION")
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError("FILE_TOO_LARGE")
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError("UNSUPPORTED_FORMAT")

    if suffix == ".txt":
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DocumentError("TEXT_DECODE_FAILED") from exc
        page = ExtractedPage(1, text, (1.0,))
        pages, reasons = [page], []
    elif suffix == ".pdf":
        pages, reasons = _pdf_pages(path)
    else:
        pages, reasons = _image_pages(path)

    confidences = [score for page in pages for score in page.confidences]
    median = statistics.median(confidences) if confidences else 0.0
    if median < MIN_MEDIAN_CONFIDENCE:
        reasons.append("DOCUMENT_LOW_CONFIDENCE")
    text = "\n\n".join(page.text for page in pages if page.text.strip()).strip()
    if len(re.sub(r"\W", "", text)) < 20:
        reasons.append("INSUFFICIENT_CLINICAL_TEXT")
    return ExtractedDocument(
        pages=tuple(pages),
        text=text,
        hold_reasons=tuple(sorted(set(reasons))),
        median_confidence=median,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


__all__ = [
    "DocumentError",
    "ExtractedDocument",
    "ExtractedPage",
    "extract_document",
]
