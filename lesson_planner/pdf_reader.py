from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Iterable

import fitz


NUMBERED_HEADING = re.compile(r"^(?:module|unit|chapter|lesson|topic|task|activity)?\s*\d+(?:\.\d+){0,4}[.)]?\s+\S", re.I)
KEY_HEADING = re.compile(r"^(?:module|unit|chapter|lesson|topic|practical\s+tasks?|objectives?|contents?|references?)\b", re.I)
NOISE = re.compile(r"^(?:page\s+)?\d+$|^©|^prepared by$", re.I)


@dataclass(slots=True)
class HeadingCandidate:
    page: int
    text: str
    score: float
    font_size: float


@dataclass(slots=True)
class PdfPage:
    number: int
    text: str
    image_count: int


@dataclass(slots=True)
class PdfExtraction:
    filename: str
    page_count: int
    pages: list[PdfPage]
    heading_candidates: list[HeadingCandidate]
    warnings: list[str]
    source_type: str = "module"
    selected_start_page: int = 1
    selected_end_page: int | None = None
    source_format: str = "pdf"
    location_kind: str = "physical_page"
    images: list[dict] | None = None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "page_count": self.page_count,
            "pages": [asdict(page) for page in self.pages],
            "heading_candidates": [asdict(item) for item in self.heading_candidates],
            "warnings": self.warnings,
            "source_type": self.source_type,
            "selected_start_page": self.selected_start_page,
            "selected_end_page": self.selected_end_page or self.page_count,
            "selected_page_count": len(self.pages),
            "source_format": self.source_format,
            "location_kind": self.location_kind,
            "images": self.images or [],
        }


def extract_pdf(
    path: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
    source_type: str = "module",
) -> PdfExtraction:
    pdf_path = Path(path)
    document = fitz.open(pdf_path)
    pages: list[PdfPage] = []
    candidates: list[HeadingCandidate] = []
    warnings: list[str] = []
    try:
        total_pages = len(document)
        selected_start, selected_end = validate_page_range(total_pages, start_page, end_page)
        for page_number in range(selected_start, selected_end + 1):
            page = document[page_number - 1]
            text = page.get_text("text", sort=True).strip()
            pages.append(PdfPage(page_number, text, len(page.get_images(full=True))))
            candidates.extend(_page_heading_candidates(page, page_number))

        if not any(page.text for page in pages):
            warnings.append("No selectable text was found in the selected pages. This PDF probably needs OCR.")
        elif sum(len(page.text) for page in pages) < page_count_threshold(len(pages)):
            warnings.append("Very little selectable text was found in the selected pages; some pages may be scans.")
    finally:
        document.close()
    return PdfExtraction(
        filename=pdf_path.name,
        page_count=total_pages,
        pages=pages,
        heading_candidates=deduplicate_candidates(candidates),
        warnings=warnings,
        source_type=source_type,
        selected_start_page=selected_start,
        selected_end_page=selected_end,
        images=[],
    )


# A selected page range keeps this small, but a teacher may read a whole book in full.
MAX_EXTRACTED_IMAGES = 300


def extract_pdf_images(
    path: str | Path,
    output_dir: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[dict]:
    """Extract useful raster figures and retain their physical PDF page numbers."""
    pdf_path = Path(path)
    destination = Path(output_dir)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    document = fitz.open(pdf_path)
    assets: list[dict] = []
    written: dict[int, Path] = {}
    try:
        selected_start, selected_end = validate_page_range(len(document), start_page, end_page)
        for page_number in range(selected_start, selected_end + 1):
            page = document[page_number - 1]
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                width, height = int(image_info[2]), int(image_info[3])
                ratio = max(width / max(height, 1), height / max(width, 1))
                # A sliver is no use to anyone, on the page or on disk. Dropping these outright
                # stops a long book's decorative fragments from filling the download folder.
                if width < 32 or height < 32 or width * height < 4_000:
                    continue
                if len(written) >= MAX_EXTRACTED_IMAGES:
                    break
                # Everything else is kept so the teacher can download it; anything unusable as
                # lesson evidence is tagged so the AI never offers it as a figure.
                skip_reason = ""
                if width < 120 or height < 90 or width * height < 20_000:
                    skip_reason = "Too small to read in a lesson plan"
                elif ratio > 8:
                    skip_reason = "Banner or rule, not a figure"
                if xref not in written:
                    payload = document.extract_image(xref)
                    extension = str(payload.get("ext") or "png")
                    asset_path = destination / f"image-{xref}.{extension}"
                    asset_path.write_bytes(payload["image"])
                    written[xref] = asset_path
                assets.append(
                    {
                        "page": page_number,
                        "xref": xref,
                        "width": width,
                        "height": height,
                        "area": width * height,
                        "path": str(written[xref].resolve()),
                        "lesson_candidate": not skip_reason,
                        "skip_reason": skip_reason,
                    }
                )
    finally:
        document.close()
    return assets


def validate_page_range(total_pages: int, start_page: int, end_page: int | None) -> tuple[int, int]:
    if total_pages < 1:
        raise ValueError("The PDF has no pages.")
    selected_start = int(start_page)
    selected_end = total_pages if end_page is None else int(end_page)
    if selected_start < 1 or selected_end < 1:
        raise ValueError("PDF page numbers must start at 1.")
    if selected_end < selected_start:
        raise ValueError("The To page must be the same as or later than the From page.")
    if selected_start > total_pages or selected_end > total_pages:
        raise ValueError(
            f"The selected range {selected_start}-{selected_end} is outside this {total_pages}-page PDF."
        )
    return selected_start, selected_end


def page_count_threshold(count: int) -> int:
    return max(120, count * 80)


def _page_heading_candidates(page: fitz.Page, page_number: int) -> list[HeadingCandidate]:
    data = page.get_text("dict", sort=True)
    lines: list[tuple[str, float, bool]] = []
    sizes: list[float] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = " ".join(str(span.get("text", "")).strip() for span in spans).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            font_size = max((float(span.get("size", 0)) for span in spans), default=0)
            is_bold = any("bold" in str(span.get("font", "")).lower() for span in spans)
            sizes.append(font_size)
            lines.append((text, font_size, is_bold))

    body_size = median(sizes) if sizes else 10.0
    result: list[HeadingCandidate] = []
    for text, font_size, is_bold in lines:
        if len(text) > 140 or NOISE.match(text):
            continue
        word_like = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text)
        explicit_heading = bool(NUMBERED_HEADING.match(text) or KEY_HEADING.match(text))
        if len(word_like) < 2 and not explicit_heading:
            continue
        score = 0.0
        if font_size >= body_size * 1.18:
            score += min(3.0, (font_size / max(body_size, 1)) * 1.4)
        if is_bold:
            score += 1.1
        if NUMBERED_HEADING.match(text):
            score += 3.0
        if KEY_HEADING.match(text):
            score += 2.5
        if text.isupper() and 3 <= len(text) <= 80:
            score += 0.8
        if text.endswith(".") and not NUMBERED_HEADING.match(text):
            score -= 0.7
        if score >= 2.3:
            result.append(HeadingCandidate(page_number, text, round(score, 2), round(font_size, 1)))

    # Some PDFs put a section number and its title in separate positioned spans.
    # The sorted plain-text view reconstructs those lines more accurately.
    for raw_line in page.get_text("text", sort=True).splitlines():
        text = re.sub(r"\s+", " ", raw_line).strip()
        looks_like_table_row = bool(re.match(r"^\d+\s+.+\s+\d+$", text))
        looks_like_broken_caption = "fig." in text.lower() and len(text) > 70
        if (
            4 <= len(text) <= 140
            and not looks_like_table_row
            and not looks_like_broken_caption
            and (NUMBERED_HEADING.match(text) or KEY_HEADING.match(text))
        ):
            result.append(HeadingCandidate(page_number, text, 4.2, round(body_size, 1)))
    return result


def deduplicate_candidates(items: Iterable[HeadingCandidate]) -> list[HeadingCandidate]:
    source = list(items)
    pages_by_text: dict[str, set[int]] = {}
    for item in source:
        normalized = re.sub(r"\W+", " ", item.text.lower()).strip()
        pages_by_text.setdefault(normalized, set()).add(item.page)
    repeated_first_page = {
        text: min(pages)
        for text, pages in pages_by_text.items()
        if len(pages) >= 3
    }

    seen: set[tuple[int, str]] = set()
    output: list[HeadingCandidate] = []
    for item in sorted(source, key=lambda value: (value.page, -value.score)):
        normalized = re.sub(r"\W+", " ", item.text.lower()).strip()
        if normalized in repeated_first_page and item.page != repeated_first_page[normalized]:
            continue
        key = (item.page, normalized)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def chunk_pages(pages: list[PdfPage], max_characters: int = 18_000) -> list[list[PdfPage]]:
    chunks: list[list[PdfPage]] = []
    current: list[PdfPage] = []
    current_size = 0
    for page in pages:
        page_size = len(page.text) + 40
        if current and current_size + page_size > max_characters:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(page)
        current_size += page_size
    if current:
        chunks.append(current)
    return chunks


def format_pages(pages: list[PdfPage], marker: str = "PAGE") -> str:
    return "\n\n".join(f"--- {marker} {page.number} ---\n{page.text}" for page in pages)
