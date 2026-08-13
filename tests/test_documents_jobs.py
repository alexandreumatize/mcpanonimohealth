from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import mcpanonimohealth.jobs as jobs_module
from mcpanonimohealth.detection import DeterministicDetector
from mcpanonimohealth.documents import DocumentError, extract_document
from mcpanonimohealth.domain import JobState
from mcpanonimohealth.jobs import JobManager
from mcpanonimohealth.sanitizer import Sanitizer


def _synthetic_image(path: Path) -> None:
    image = Image.new("RGB", (1500, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    draw.multiline_text(
        (35, 40),
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnostico: artrite reumatoide",
        fill="black",
        font=font,
        spacing=28,
    )
    image.save(path)


def test_real_local_ocr_extracts_synthetic_image(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.png"
    _synthetic_image(path)
    result = extract_document(path)
    assert result.pages
    assert result.median_confidence >= 0.90
    assert "Paciente" in result.text
    assert "529.982.247-25" in result.text


def test_rejects_symlink_and_oversized_input(tmp_path: Path) -> None:
    original = tmp_path / "synthetic.txt"
    original.write_text("caso inteiramente sintetico", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(original)
    try:
        extract_document(link)
    except DocumentError as error:
        assert str(error) == "INVALID_SELECTION"
    else:
        raise AssertionError("symlink deveria ser rejeitado")


def test_job_releases_only_sanitized_text(monkeypatch, tmp_path: Path) -> None:
    raw = tmp_path / "synthetic.txt"
    raw.write_text(
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnóstico: artrite reumatoide.",
        encoding="utf-8",
    )
    monkeypatch.setattr(jobs_module, "select_local_file", lambda: raw)
    manager = JobManager()
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001

    public = manager.create_from_selection()
    assert public["state"] == JobState.PASS.value
    serialized = str(public)
    assert str(raw) not in serialized
    assert raw.name not in serialized
    clean = manager.get_clean_text(str(public["job_id"]))
    assert "Maria da Silva" not in clean
    assert "529.982.247-25" not in clean


def test_discard_makes_text_unavailable(monkeypatch, tmp_path: Path) -> None:
    raw = tmp_path / "synthetic.txt"
    raw.write_text(
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnóstico: artrite reumatoide.",
        encoding="utf-8",
    )
    monkeypatch.setattr(jobs_module, "select_local_file", lambda: raw)
    manager = JobManager()
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001
    public = manager.create_from_selection()
    job_id = str(public["job_id"])
    manager.discard(job_id)
    try:
        manager.get_clean_text(job_id)
    except KeyError:
        pass
    else:
        raise AssertionError("job descartado não pode liberar texto")

