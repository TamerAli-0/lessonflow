from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from docx import Document
from docx.image.image import Image
from docx.table import Table
from docx.text.paragraph import Paragraph

from .office import OfficeConversionError, convert_doc_to_docx
from .pdf_reader import HeadingCandidate, PdfExtraction, PdfPage, deduplicate_candidates, extract_pdf, extract_pdf_images


SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".docx", ".doc"}
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
VML_NS = "urn:schemas-microsoft-com:vml"


def extract_source(
    path: str | Path,
    *,
    source_type: str = "module",
    start_page: int = 1,
    end_page: int | None = None,
) -> PdfExtraction:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(source_path, start_page=start_page, end_page=end_page, source_type=source_type)
    if suffix not in {".docx", ".doc"}:
        raise ValueError("Upload a PDF or Word document (.pdf, .docx, or .doc).")
    if start_page != 1 or end_page is not None:
        raise ValueError("Physical page ranges are available for PDFs only. Word documents are read in full.")
    word_path = prepare_word_document(source_path)
    return extract_word_document(word_path, source_type=source_type, original_filename=source_path.name)


def extract_source_images(
    path: str | Path,
    output_dir: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[dict]:
    source_path = Path(path)
    if source_path.suffix.lower() == ".pdf":
        return extract_pdf_images(source_path, output_dir, start_page=start_page, end_page=end_page)
    return extract_word_images(prepare_word_document(source_path), output_dir)


def prepare_word_document(path: str | Path) -> Path:
    source_path = Path(path)
    if source_path.suffix.lower() == ".docx":
        return source_path
    if source_path.suffix.lower() != ".doc":
        raise ValueError("The Word document must be a .docx or .doc file.")
    converted = source_path.with_name(f"{source_path.stem}-converted.docx")
    if converted.is_file():
        return converted
    try:
        return convert_doc_to_docx(source_path, converted)
    except OfficeConversionError as exc:
        raise ValueError(str(exc)) from exc


def extract_word_document(
    path: str | Path,
    *,
    source_type: str = "module",
    original_filename: str | None = None,
    max_characters_per_part: int = 14_000,
) -> PdfExtraction:
    word_path = Path(path)
    document = Document(word_path)
    parts: list[PdfPage] = []
    candidates: list[HeadingCandidate] = []
    current: list[str] = []
    current_size = 0
    current_number = 1

    def flush() -> None:
        nonlocal current, current_size, current_number
        text = "\n".join(current).strip()
        if text:
            parts.append(PdfPage(current_number, text, 0))
            current_number += 1
        current = []
        current_size = 0

    for block in document.iter_inner_content():
        text = _word_block_text(block)
        if not text:
            continue
        if current and current_size + len(text) + 2 > max_characters_per_part:
            flush()
        if isinstance(block, Paragraph) and _is_word_heading(block):
            candidates.append(HeadingCandidate(current_number, text[:140], 5.0, round(_paragraph_font_size(block), 1)))
        elif isinstance(block, Table):
            candidates.extend(_word_table_heading_candidates(block, current_number))
        current.append(text)
        current_size += len(text) + 2
    flush()

    warnings: list[str] = []
    if not parts:
        warnings.append("No readable text was found in this Word document.")
        parts = [PdfPage(1, "", 0)]
    return PdfExtraction(
        filename=original_filename or word_path.name,
        page_count=len(parts),
        pages=parts,
        heading_candidates=deduplicate_candidates(candidates),
        warnings=warnings,
        source_type=source_type,
        selected_start_page=1,
        selected_end_page=len(parts),
        source_format="docx",
        location_kind="document_part",
        images=[],
    )


MAX_EXTRACTED_IMAGES = 300


def extract_word_images(path: str | Path, output_dir: str | Path) -> list[dict]:
    destination = Path(output_dir)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    assets: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        occurrences, document_text = _word_image_occurrences(archive)
        for name, position in occurrences:
            payload = archive.read(name)
            try:
                image = Image.from_blob(payload)
                width, height = int(image.px_width), int(image.px_height)
            except Exception:
                continue
            ratio = max(width / max(height, 1), height / max(width, 1))
            if width < 32 or height < 32 or width * height < 4_000:
                continue
            if len(assets) >= MAX_EXTRACTED_IMAGES:
                break
            # Every image is kept so the teacher can download the lot. Anything that is not
            # lesson evidence is tagged instead of discarded, so the AI never offers it as a
            # figure while the teacher can still see and save it.
            skip_reason = ""
            if width < 120 or height < 90 or width * height < 20_000:
                skip_reason = "Too small to read in a lesson plan"
            elif ratio > 8:
                skip_reason = "Banner or rule, not a figure"
            elif position == 0 or (position < 250 and width >= 500 and ratio >= 1.35):
                # Header/footer media is already absent from `occurrences`; this rejects a
                # leading cover logo such as the supplied module's institutional branding.
                skip_reason = "Cover or institutional branding"
            index = len(assets) + 1
            suffix = Path(name).suffix.lower() or ".png"
            asset_path = destination / f"image-{index}{suffix}"
            asset_path.write_bytes(payload)
            assets.append({
                "page": max(1, (position // 14_000) + 1),
                "xref": index,
                "width": width,
                "height": height,
                "area": width * height,
                "path": str(asset_path.resolve()),
                "context": _nearby_text(document_text, position),
                "lesson_candidate": not skip_reason,
                "skip_reason": skip_reason,
            })
    return assets


def _word_image_occurrences(archive: zipfile.ZipFile) -> tuple[list[tuple[str, int]], str]:
    relationship_root = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
    relationships = {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in relationship_root.findall(f"{{{RELATIONSHIP_NS}}}Relationship")
        if item.attrib.get("Type", "").endswith("/image")
    }
    document_root = ElementTree.fromstring(archive.read("word/document.xml"))
    text_parts: list[str] = []
    position = 0
    raw_occurrences: list[tuple[str, int]] = []
    for element in document_root.iter():
        if element.tag == f"{{{WORD_NS}}}t":
            value = element.text or ""
            text_parts.append(value)
            position += len(value)
            continue
        relationship_id = None
        if element.tag == f"{{{DRAWING_NS}}}blip":
            relationship_id = element.attrib.get(f"{{{OFFICE_REL_NS}}}embed")
        elif element.tag == f"{{{VML_NS}}}imagedata":
            relationship_id = element.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        target = relationships.get(relationship_id or "", "")
        if target:
            archive_name = str(Path("word") / target).replace("\\", "/")
            if archive_name in archive.namelist():
                raw_occurrences.append((archive_name, position))

    seen: set[str] = set()
    occurrences: list[tuple[str, int]] = []
    for name, image_position in raw_occurrences:
        if name not in seen:
            seen.add(name)
            occurrences.append((name, image_position))
    return occurrences, "".join(text_parts)


def _nearby_text(text: str, position: int) -> str:
    start = max(0, position - 260)
    end = min(len(text), position + 700)
    return " ".join(text[start:end].split())


def _word_block_text(block: Paragraph | Table) -> str:
    if isinstance(block, Paragraph):
        return " ".join(block.text.split())
    rows: list[str] = []
    for row in block.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _is_word_heading(paragraph: Paragraph) -> bool:
    style_name = str(paragraph.style.name or "").lower() if paragraph.style else ""
    text = " ".join(paragraph.text.split())
    is_bold = any(run.bold for run in paragraph.runs if run.text.strip())
    structured_label = bool(
        re.match(r"^(?:\d+(?:\.\d+)*[.:]?\s+\S|activity\s+\d|exercise\s+\d)", text, flags=re.I)
        or text.lower().rstrip(":") in {
            "introduction", "module contents", "module objectives", "supplementary resources",
            "supplementary recourses", "references",
        }
    )
    return style_name.startswith("heading") or _paragraph_font_size(paragraph) >= 14 or (is_bold and structured_label)


def _word_table_heading_candidates(table: Table, part_number: int) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    seen_cells: set[object] = set()
    for row in table.rows:
        for cell in row.cells:
            identity = cell._tc
            if identity in seen_cells:
                continue
            seen_cells.add(identity)
            for paragraph in cell.paragraphs:
                text = " ".join(paragraph.text.split())
                if not text or not _is_word_heading(paragraph):
                    continue
                heading_text = _leading_emphasized_text(paragraph) or (text if len(text) <= 140 else "")
                if not heading_text:
                    continue
                candidates.append(
                    HeadingCandidate(
                        page=part_number,
                        text=heading_text[:140],
                        score=5.0,
                        font_size=round(_paragraph_font_size(paragraph), 1),
                    )
                )
    return candidates


def _leading_emphasized_text(paragraph: Paragraph) -> str:
    pieces: list[str] = []
    for run in paragraph.runs:
        value = " ".join(run.text.split())
        if not value:
            continue
        if run.bold:
            pieces.append(value)
            continue
        if pieces:
            break
    value = " ".join(pieces).strip()
    return value if len(value) <= 140 else ""


def _paragraph_font_size(paragraph: Paragraph) -> float:
    sizes = [run.font.size.pt for run in paragraph.runs if run.font.size is not None]
    return max(sizes, default=11.0)
