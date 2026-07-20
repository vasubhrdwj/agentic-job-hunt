"""Security and extraction coverage for first-class resume uploads."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject

from job_hunt_agent.resume_ingestion import (
    MAX_DOCX_UNCOMPRESSED_BYTES,
    MAX_PDF_PAGES,
    ResumeIngestionError,
    parse_resume_upload,
)


SAMPLE_RESUME = """Vasu Example
PROFESSIONAL SUMMARY
Backend engineer building reliable event-driven systems.

PROFESSIONAL EXPERIENCE
Software Engineer | July 2025 – Present
Example Labs | Gurugram
• Built a Kafka ingestion pipeline processing 250+ events with AWS Lambda and OAuth 2.0.
• Reduced identity onboarding time by 60% by deploying a SCIM provisioning service.

EDUCATION
B.Tech in Computer Science, 2021 – 2025

PROJECTS
Incident Commander
• Trained an incident-response model and lifted hard-task performance from 0.35 to 0.90.

SKILLS
Python, FastAPI, Kafka, Docker, AWS, OAuth 2.0, SCIM

CERTIFICATIONS
AWS Academy Graduate
"""


def test_txt_import_extracts_grounded_profile_evidence_and_sections() -> None:
    parsed = parse_resume_upload(
        SAMPLE_RESUME.encode(),
        filename="backend-resume.txt",
        content_type="text/plain; charset=utf-8",
        as_of=date(2026, 7, 20),
    )

    assert parsed.current_title == "Software Engineer"
    assert parsed.current_location is None
    assert parsed.years_of_experience == 1.1
    assert parsed.sections == (
        "profile",
        "experience",
        "education",
        "projects",
        "skills",
        "certifications",
    )
    assert len(parsed.evidence) == 3
    assert all(item.statement == item.source_excerpt for item in parsed.evidence)
    assert all(item.source_excerpt in parsed.content for item in parsed.evidence)
    assert {"Python", "Kafka", "AWS", "SCIM"}.issubset(parsed.skills)
    assert "Gurugram" not in (parsed.current_location or "")
    assert any("estimated" in warning for warning in parsed.warnings)


def test_docx_import_reads_tables_and_list_paragraphs_in_document_order() -> None:
    document = Document()
    document.add_paragraph("PROFESSIONAL EXPERIENCE")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Backend Engineer"
    table.cell(0, 1).text = "Jan 2024 – Present"
    table.cell(1, 0).text = "Example Labs"
    table.cell(1, 1).text = "Bengaluru"
    document.add_paragraph(
        "Built a reliable API platform and reduced p95 latency by 45% using Python.",
        style="List Bullet",
    )
    document.add_paragraph("SKILLS")
    document.add_paragraph("Python, PostgreSQL, Docker")
    payload = BytesIO()
    document.save(payload)

    parsed = parse_resume_upload(
        payload.getvalue(),
        filename="resume.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        as_of=date(2026, 7, 20),
    )

    assert "Backend Engineer | Jan 2024 – Present" in parsed.content
    assert parsed.current_title == "Backend Engineer"
    assert len(parsed.evidence) == 1
    assert parsed.evidence[0].statement.startswith("• Built a reliable API platform")
    assert "Python" in parsed.evidence[0].skills


def test_pdf_import_uses_layout_text_and_enforces_page_limit() -> None:
    parsed = parse_resume_upload(
        _text_pdf(SAMPLE_RESUME),
        filename="resume.pdf",
        content_type="application/octet-stream",
        as_of=date(2026, 7, 20),
    )

    assert parsed.page_count == 1
    assert parsed.current_title == "Software Engineer"
    assert "experience" in parsed.sections
    assert len(parsed.evidence) >= 2

    writer = PdfWriter()
    for _ in range(MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=612, height=792)
    oversized_pages = BytesIO()
    writer.write(oversized_pages)
    with pytest.raises(ResumeIngestionError, match="pages or fewer") as exc_info:
        parse_resume_upload(
            oversized_pages.getvalue(),
            filename="long.pdf",
            content_type="application/pdf",
        )
    assert exc_info.value.code == "resume_pdf_too_many_pages"


@pytest.mark.parametrize(
    ("filename", "media_type", "payload", "code"),
    [
        ("resume.pages", "application/octet-stream", b"content", "resume_type_unsupported"),
        ("resume.pdf", "text/plain", b"%PDF-invalid", "resume_type_mismatch"),
        ("resume.pdf", "application/pdf", b"not a pdf", "resume_pdf_invalid"),
        ("resume.txt", "text/plain", b"binary\x00content", "resume_text_invalid"),
        ("resume.txt", "text/plain", b"\xff\xfe", "resume_text_invalid"),
    ],
)
def test_invalid_uploads_fail_with_specific_safe_codes(
    filename: str,
    media_type: str,
    payload: bytes,
    code: str,
) -> None:
    with pytest.raises(ResumeIngestionError) as exc_info:
        parse_resume_upload(payload, filename=filename, content_type=media_type)
    assert exc_info.value.code == code


def test_encrypted_and_image_only_pdfs_get_recovery_guidance() -> None:
    encrypted = PdfWriter()
    encrypted.add_blank_page(width=612, height=792)
    encrypted.encrypt("secret")
    encrypted_bytes = BytesIO()
    encrypted.write(encrypted_bytes)
    with pytest.raises(ResumeIngestionError, match="Password-protected") as encrypted_error:
        parse_resume_upload(
            encrypted_bytes.getvalue(),
            filename="locked.pdf",
            content_type="application/pdf",
        )
    assert encrypted_error.value.code == "resume_pdf_encrypted"

    blank = PdfWriter()
    blank.add_blank_page(width=612, height=792)
    blank_bytes = BytesIO()
    blank.write(blank_bytes)
    with pytest.raises(ResumeIngestionError, match="scanned or image-only") as blank_error:
        parse_resume_upload(
            blank_bytes.getvalue(),
            filename="scan.pdf",
            content_type="application/pdf",
        )
    assert blank_error.value.code == "resume_pdf_needs_ocr"


def test_docx_zip_bomb_metadata_is_rejected_before_document_parsing() -> None:
    payload = BytesIO()
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", b" " * (MAX_DOCX_UNCOMPRESSED_BYTES + 1))

    with pytest.raises(ResumeIngestionError, match="safe processing limit") as exc_info:
        parse_resume_upload(
            payload.getvalue(),
            filename="bomb.docx",
            content_type="application/zip",
        )
    assert exc_info.value.code == "resume_docx_unsafe"


def test_unicode_is_normalized_without_importing_an_office_as_home() -> None:
    payload = SAMPLE_RESUME.replace("Vasu Example", "Vasu\u200b Example").replace(
        "Kafka", "Ｋａｆｋａ", 1
    )
    parsed = parse_resume_upload(
        payload.encode(),
        filename="resume.txt",
        content_type=None,
        as_of=date(2026, 7, 20),
    )

    assert "\u200b" not in parsed.content
    assert "Kafka" in parsed.content
    assert parsed.current_location is None


def _text_pdf(text: str) -> bytes:
    """Create a small Type1-font PDF fixture without another test dependency."""

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    commands = ["BT", "/F1 9 Tf", "42 755 Td", "11 TL"]
    for index, line in enumerate(text.splitlines()):
        if index:
            commands.append("T*")
        escaped = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("•", "-")
            .replace("–", "-")
        )
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("latin-1", "replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()

