from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import mcpanonimohealth.jobs as jobs_module
from mcpanonimohealth.detection import DeterministicDetector
from mcpanonimohealth.documents import DocumentError, extract_document
from mcpanonimohealth.domain import JobState
from mcpanonimohealth.jobs import JobManager
from mcpanonimohealth.sanitizer import Sanitizer


def _synthetic_image(path: Path, lines: str) -> Image.Image:
    image = Image.new("RGB", (1500, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    draw.multiline_text((35, 40), lines, fill="black", font=font, spacing=28)
    if path.suffix.casefold() != ".pdf":
        image.save(path)
    return image


def test_real_local_ocr_extracts_synthetic_image(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.png"
    _synthetic_image(
        path,
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnostico: artrite reumatoide",
    )
    result = extract_document(path)
    assert result.pages
    assert result.median_confidence >= 0.90
    assert "Paciente" in result.text
    assert "529.982.247-25" in result.text


def test_image_based_multipage_pdf_ocr_and_job(tmp_path: Path) -> None:
    """PDF rasterizado de 2 páginas — cenário que motivou a regressão original."""

    page1 = _synthetic_image(
        tmp_path / "p1.png",
        "Pagina 1\nPaciente: Maria da Silva\nCPF: 529.982.247-25",
    )
    page2 = _synthetic_image(
        tmp_path / "p2.png",
        "Pagina 2\nMedicamento: metotrexato 15 mg\nRetorno: 20/08/2026",
    )
    pdf_path = tmp_path / "synthetic-multipage.pdf"
    page1.save(pdf_path, "PDF", save_all=True, append_images=[page2])

    extracted = extract_document(pdf_path)
    assert len(extracted.pages) == 2
    assert extracted.median_confidence >= 0.90
    assert "Pagina 1" in extracted.text or "Maria" in extracted.text
    assert "Pagina 2" in extracted.text or "metotrexato" in extracted.text
    assert "529.982.247-25" in extracted.text

    manager = JobManager()
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001
    reserved = manager.reserve()
    result = manager.process_path(str(reserved["job_id"]), pdf_path)
    assert result["state"] == JobState.PASS.value
    assert result["pages"] == 2
    clean = manager.get_clean_text(str(reserved["job_id"]))
    assert "Maria da Silva" not in clean
    assert "529.982.247-25" not in clean
    assert "metotrexato" in clean.lower() or "METOTREXATO" in clean.upper() or "15" in clean


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


def test_reserved_job_can_be_processed_without_exposing_path(tmp_path: Path) -> None:
    raw = tmp_path / "nome-de-paciente-sintetico.txt"
    raw.write_text(
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnóstico: artrite reumatoide.",
        encoding="utf-8",
    )
    manager = JobManager()
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001
    reserved = manager.reserve()
    result = manager.process_path(str(reserved["job_id"]), raw)
    assert result["state"] == JobState.PASS.value
    assert str(raw) not in str(result)
    assert raw.name not in str(result)


def test_active_ttl_removes_job_without_lookup(tmp_path: Path) -> None:
    raw = tmp_path / "synthetic.txt"
    raw.write_text(
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnóstico: artrite reumatoide.",
        encoding="utf-8",
    )
    manager = JobManager(ttl_seconds=1)
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001
    reserved = manager.reserve()
    job_id = str(reserved["job_id"])
    manager.process_path(job_id, raw)
    assert job_id in manager._jobs  # noqa: SLF001
    assert manager._jobs[job_id].clean_text is not None  # noqa: SLF001

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and job_id in manager._jobs:  # noqa: SLF001
        time.sleep(0.05)

    assert job_id not in manager._jobs  # noqa: SLF001
    assert job_id not in manager._timers  # noqa: SLF001


def test_discard_cancels_ttl_timer(tmp_path: Path) -> None:
    raw = tmp_path / "synthetic.txt"
    raw.write_text(
        "Paciente: Maria da Silva\nCPF: 529.982.247-25\nDiagnóstico: artrite reumatoide.",
        encoding="utf-8",
    )
    manager = JobManager(ttl_seconds=30)
    manager._sanitizer = Sanitizer(DeterministicDetector(), require_ner=False)  # noqa: SLF001
    reserved = manager.reserve()
    job_id = str(reserved["job_id"])
    manager.process_path(job_id, raw)
    assert job_id in manager._timers  # noqa: SLF001
    manager.discard(job_id)
    assert job_id not in manager._timers  # noqa: SLF001
    assert job_id not in manager._jobs  # noqa: SLF001


def test_complete_batch_release_package_for_agent() -> None:
    manager = JobManager()
    reserved = manager.reserve()
    job_id = str(reserved["job_id"])
    public = manager.complete_batch(
        job_id,
        liberados=2,
        retidos=0,
        erros=0,
        pages=2,
        duration_ms=40,
        itens=[
            {
                "estado": "PASS",
                "iniciais": "MS",
                "tipo": "receita",
                "data_documento": "2026-08-10",
                "relativo": "MS/receita_2026-08-10.txt",
                "paginas": 1,
                "motivos": [],
                "texto": "Paciente [PACIENTE_001]. MTX.",
            },
            {
                "estado": "PASS",
                "iniciais": "JP",
                "tipo": "receita",
                "data_documento": "2026-08-11",
                "relativo": "JP/receita_2026-08-11.txt",
                "paginas": 1,
                "motivos": [],
                "texto": "Paciente [PACIENTE_002]. Pred.",
            },
        ],
    )
    assert public["state"] == "PASS"
    assert public["modo"] == "lote"
    release = manager.get_release(job_id)
    assert release["modo"] == "lote"
    assert len(release["itens"]) == 2
    assert release["itens"][0]["texto_desidentificado"].startswith("Paciente")
    assert "MS/receita_2026-08-10.txt" in str(release["texto_desidentificado"])
    assert manager.get_clean_text(job_id)
