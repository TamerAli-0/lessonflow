from __future__ import annotations

import shutil
from pathlib import Path

import fitz

from .docx_exporter import export_lesson_bundle
from .office import OfficeConversionError, convert_to_pdf


class PdfExportError(RuntimeError):
    """Raised when the office renderer cannot create a PDF."""


def export_pdf_bundle(
    template_path: str | Path,
    output_dir: str | Path,
    analysis: dict,
    plan: dict,
    image_assets: list[dict] | None = None,
    force_opening_page_break: bool = True,
) -> tuple[Path, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    export_lesson_bundle(
        template_path, output / "source-docx.zip", analysis, plan, image_assets,
        force_opening_page_break=force_opening_page_break,
    )
    documents_dir = output / "lesson-documents"
    pdf_dir = output / "lesson-pdfs"
    if pdf_dir.exists():
        shutil.rmtree(pdf_dir)
    pdf_dir.mkdir(parents=True)

    for document in sorted(documents_dir.glob("*.docx")):
        try:
            convert_to_pdf(document, pdf_dir)
        except OfficeConversionError as exc:
            raise PdfExportError(str(exc)) from exc

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise PdfExportError("No lesson PDFs were created.")
    combined_path = output / "generated-lesson-plans.pdf"
    if combined_path.exists():
        combined_path.unlink()
    combined = fitz.open()
    for pdf_path in pdfs:
        source = fitz.open(pdf_path)
        combined.insert_pdf(source)
        source.close()
    combined.set_metadata(
        {
            "title": str(analysis.get("module_title") or "Generated lesson plans"),
            "subject": "Generated weekly lesson plans",
            "author": "LessonFlow",
            "creator": "LessonFlow",
        }
    )
    combined.save(combined_path, garbage=4, deflate=True)
    page_count = len(combined)
    combined.close()

    # The designed layout puts a hard break before Opening so page 2 starts there. Once the plan
    # has grown past two pages that break only strands a near-empty page, so rebuild once letting
    # the content flow. Plans that still fit two pages keep the designed layout untouched.
    if force_opening_page_break and page_count > 2:
        return export_pdf_bundle(
            template_path, output_dir, analysis, plan, image_assets, force_opening_page_break=False
        )
    return combined_path, "application/pdf"


def render_pdf_pages(pdf_path: str | Path, output_dir: str | Path, scale: float = 1.35) -> list[Path]:
    destination = Path(output_dir)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    pdf = fitz.open(pdf_path)
    pages: list[Path] = []
    matrix = fitz.Matrix(scale, scale)
    for index, page in enumerate(pdf, start=1):
        path = destination / f"page-{index}.png"
        page.get_pixmap(matrix=matrix, alpha=False).save(path)
        pages.append(path)
    pdf.close()
    return pages


def build_pdf_hotspots(pdf_path: str | Path, plan: dict) -> list[list[dict]]:
    lesson = (plan.get("lessons") or [{}])[0]
    stage_order = {
        "warm_up": 0,
        "focused_instruction": 1,
        "guided_instruction": 2,
        "collaborative_learning": 3,
        "independent_learning": 4,
        "progress_check": 5,
    }
    selected_images = lesson.get("selected_images")
    if not isinstance(selected_images, list):
        default_field = str(lesson.get("recommended_image_stage") or "focused_instruction")
        selected_images = [
            {"xref": xref, "field": default_field}
            for xref in lesson.get("selected_image_xrefs", [])
        ]
    sorted_placements = [
        item for item in sorted(selected_images, key=lambda item: stage_order.get(str(item.get("field")), 99))
        if isinstance(item, dict)
    ]
    image_cursor = 0
    labels = {
        "title": "Topic & pages",
        "resources": "Resources",
        "kpis": "KPIs",
        "c21_skills": "C21 skills",
        "success_criteria": "Success criteria",
        "cross_curricular": "Cross-curricular",
        "warm_up": "Warm up",
        "focused_instruction": "Focused instruction",
        "guided_instruction": "Guided instruction",
        "collaborative_learning": "Collaborative learning",
        "independent_learning": "Independent learning",
        "progress_check": "Progress check",
        "home_learning": "Home learning",
        "reflection": "Reflection",
    }
    block_heading_queries = {
        "title": "LESSON/UNIT/TOPIC & PAGES",
        "resources": "RESOURCES",
        "kpis": "MAIN LEARNING FOCUS",
        "c21_skills": "C21ST SKILLS",
        "success_criteria": "SUCCESS CRITERIA",
        "cross_curricular": "NUMERACY, LITERACY",
        "home_learning": "Home Learning",
        "reflection": "REFLECTION",
    }
    sources = {
        "title": [lesson.get("title", ""), lesson.get("page_range", "")],
        "reflection": [lesson.get("reflection", "")],
    }
    for field in labels:
        if field not in sources:
            value = lesson.get(field, [])
            sources[field] = value if isinstance(value, list) else [value]

    pdf = fitz.open(pdf_path)
    all_pages: list[list[dict]] = []
    for page_index, page in enumerate(pdf):
        page_items: list[dict] = []
        vertical_lines, horizontal_lines = _page_table_lines(page)
        text_blocks = [
            (fitz.Rect(block[:4]), str(block[4]))
            for block in page.get_text("blocks", sort=True)
            if str(block[4]).strip()
        ]
        for field, texts in sources.items():
            target_x = None
            cell_rect = None
            rendered_capacity = None
            heading_query = block_heading_queries.get(field)
            if heading_query:
                heading_rects = page.search_for(heading_query)
                if not heading_rects:
                    continue
                target_x = heading_rects[0].x0
                heading_box = fitz.Rect(heading_rects[0])
                for rect in heading_rects[1:]:
                    heading_box |= rect
                cell_rect = _enclosing_table_cell(heading_box, vertical_lines, horizontal_lines)
                if cell_rect is not None:
                    rendered_capacity = _rendered_cell_capacity(cell_rect)
            located = []
            for item_index, text in enumerate(texts):
                cleaned = " ".join(str(text).split())
                if not cleaned:
                    continue
                words = cleaned.split()

                def _search(query: str) -> list:
                    rects = page.search_for(query)
                    if target_x is not None:
                        rects = [rect for rect in rects if abs(rect.x0 - target_x) < 90]
                    return rects

                found_rects = _search(cleaned)
                used_fallback = False
                if not found_rects:
                    # A dash or space the renderer wrote differently breaks an exact match and
                    # used to leave the outline stranded on a prefix. Try a plain-text variant
                    # of the whole item before giving up on matching all of it.
                    simplified = _simplify_for_search(cleaned)
                    if simplified != cleaned:
                        found_rects = _search(simplified)
                if not found_rects:
                    used_fallback = True
                    queries = []
                    if len(words) >= 8:
                        queries.append(" ".join(words[:8]))
                    if len(words) >= 5:
                        queries.append(" ".join(words[:5]))
                    queries.append(" ".join(words[:3]))
                    for query in queries:
                        found_rects = _search(query) or _search(_simplify_for_search(query))
                        if found_rects:
                            break
                if not found_rects:
                    continue
                geometry = fitz.Rect(found_rects[0])
                for found in found_rects[1:]:
                    geometry |= found
                start = min(found_rects, key=lambda rect: (rect.y0, rect.x0))
                containing = [
                    block_rect for block_rect, _ in text_blocks
                    if block_rect.intersects(start)
                ]
                block = min(containing, key=lambda rect: rect.get_area()) if containing else fitz.Rect(start)
                located.append(
                    {
                        "index": item_index,
                        "start": start,
                        "block": block,
                        "geometry": geometry,
                        "fallback": used_fallback,
                    }
                )

            for item in located:
                if not item["fallback"]:
                    continue
                later_starts = [
                    other["start"].y0 for other in located
                    if other["block"] == item["block"] and other["start"].y0 > item["start"].y0 + 0.5
                ]
                bottom = min(later_starts) - 1 if later_starts else item["block"].y1
                item["geometry"] = fitz.Rect(
                    item["geometry"].x0,
                    item["geometry"].y0,
                    item["block"].x1,
                    bottom,
                )

            if located:
                combined = fitz.Rect(located[0]["geometry"])
                for item in located[1:]:
                    combined |= item["geometry"]
                if cell_rect is not None and field in {
                    "kpis", "c21_skills", "success_criteria", "cross_curricular",
                    "home_learning", "reflection",
                }:
                    for block_rect, _ in text_blocks:
                        center = fitz.Point((block_rect.x0 + block_rect.x1) / 2, (block_rect.y0 + block_rect.y1) / 2)
                        if cell_rect.contains(center) and block_rect.y0 >= combined.y0 - 1:
                            combined |= block_rect
                expanded = fitz.Rect(combined.x0 - 3, combined.y0 - 2, combined.x1 + 3, combined.y1 + 2) & page.rect
                page_items.append(
                    _normalized_hotspot(
                        expanded,
                        page.rect,
                        field,
                        labels[field],
                        capacity=rendered_capacity,
                    )
                )

        for info in sorted(page.get_image_info(xrefs=True), key=lambda item: (item["bbox"][1], item["bbox"][0])):
            rect = fitz.Rect(info["bbox"])
            # Branding sits small in the header band. Filtering on horizontal position instead
            # used to strand a lesson image dragged to the left: it lost its hotspot and could
            # never be selected again, so only the header band and size exclude an image now.
            in_header_band = rect.y0 < page.rect.height * 0.10 and rect.get_area() < 20_000
            if rect.get_area() > 700 and not in_header_band:
                expanded = fitz.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3) & page.rect
                image_selection = sorted_placements[image_cursor] if image_cursor < len(sorted_placements) else {}
                image_field = str(image_selection.get("field") or "focused_instruction")
                image_cursor += 1
                label = labels.get(image_field, "Lesson") + " image"
                hotspot = _normalized_hotspot(expanded, page.rect, image_field, label)
                hotspot["kind"] = "image"
                if str(image_selection.get("xref", "")).isdigit():
                    hotspot["source_xref"] = int(image_selection["xref"])
                container = _enclosing_table_cell(rect, vertical_lines, horizontal_lines)
                if container is not None:
                    hotspot["container"] = {
                        "left": round((container.x0 / page.rect.width) * 100, 3),
                        "top": round((container.y0 / page.rect.height) * 100, 3),
                        "width": round((container.width / page.rect.width) * 100, 3),
                        "height": round((container.height / page.rect.height) * 100, 3),
                    }
                page_items.append(hotspot)
        all_pages.append(page_items)
    pdf.close()
    return all_pages


def _normalized_hotspot(
    rect: fitz.Rect,
    page_rect: fitz.Rect,
    field: str,
    label: str,
    *,
    capacity: int | None = None,
) -> dict:
    result = {
        "field": field,
        "label": label,
        "left": round((rect.x0 / page_rect.width) * 100, 3),
        "top": round((rect.y0 / page_rect.height) * 100, 3),
        "width": round((rect.width / page_rect.width) * 100, 3),
        "height": round((rect.height / page_rect.height) * 100, 3),
    }
    if capacity:
        result["capacity"] = capacity
    return result


def _page_table_lines(page: fitz.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    vertical: list[tuple[float, float, float]] = []
    horizontal: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if not item or item[0] != "l" or len(item) < 3:
                continue
            start, end = item[1], item[2]
            if abs(start.x - end.x) < 0.5 and abs(start.y - end.y) > 8:
                vertical.append((start.x, min(start.y, end.y), max(start.y, end.y)))
            elif abs(start.y - end.y) < 0.5 and abs(start.x - end.x) > 8:
                horizontal.append((start.y, min(start.x, end.x), max(start.x, end.x)))
    return vertical, horizontal


def _simplify_for_search(value: str) -> str:
    """Plain-text form of an item, for matching against what the renderer actually drew."""
    simplified = "".join(" " if ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F else ch for ch in value)
    for dash in ("\u2013", "\u2014", "\u2011", "\u2012", "\u2212"):
        simplified = simplified.replace(dash, "-")
    simplified = simplified.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    simplified = simplified.replace("\u201c", '"').replace("\u201d", '"').replace("\u2026", "...")
    return " ".join(simplified.split())


def _enclosing_table_cell(
    heading: fitz.Rect,
    vertical: list[tuple[float, float, float]],
    horizontal: list[tuple[float, float, float]],
) -> fitz.Rect | None:
    center_x = (heading.x0 + heading.x1) / 2
    center_y = (heading.y0 + heading.y1) / 2
    lefts = [x for x, y0, y1 in vertical if x <= heading.x0 + 1 and y0 - 1 <= center_y <= y1 + 1]
    rights = [x for x, y0, y1 in vertical if x >= heading.x1 - 1 and y0 - 1 <= center_y <= y1 + 1]
    tops = [y for y, x0, x1 in horizontal if y <= center_y and x0 - 1 <= center_x <= x1 + 1]
    bottoms = [y for y, x0, x1 in horizontal if y >= center_y and x0 - 1 <= center_x <= x1 + 1]
    if not lefts or not rights or not tops or not bottoms:
        return None
    left, right = max(lefts), min(rights)
    top, bottom = max(tops), min(bottoms)
    if right - left < 20 or bottom - top < 12:
        return None
    return fitz.Rect(left, top, right, bottom)


def _rendered_cell_capacity(cell: fitz.Rect) -> int:
    usable_width = max(cell.width - 12, 20)
    usable_height = max(cell.height - 24, 12)
    return max(80, int((usable_width / 4.8) * (usable_height / 13.0)))
