from __future__ import annotations

import re

from .ai_providers import ProviderConfig, generate_json
from .pdf_reader import PdfExtraction, chunk_pages
from .prompts import (
    ANALYSIS_SYSTEM,
    LESSON_SYSTEM,
    analysis_prompt,
    lesson_generation_prompt,
    merge_analysis_prompt,
    image_selection_prompt,
    word_image_selection_prompt,
    rewrite_prompt,
    rewrite_suggestions_prompt,
    section_revision_prompt,
)
from .prompts import IMAGE_SELECTION_SYSTEM, REWRITE_SUGGESTION_SYSTEM, REWRITE_SYSTEM, SECTION_REVISION_SYSTEM
from .schemas import (
    GenerationOptions,
    PlanningSettings,
    ValidationError,
    _clean_topic_title,
    validate_lesson_plan,
    validate_module_analysis,
)


# The fixed template holds one plan, and these are the per-section maxima the prompt states.
MERGE_ITEM_LIMITS = {
    "resources": 5, "kpis": 4, "c21_skills": 4, "success_criteria": 4, "cross_curricular": 3,
    "warm_up": 3, "focused_instruction": 3, "guided_instruction": 3, "collaborative_learning": 3,
    "independent_learning": 3, "progress_check": 3, "home_learning": 2,
}


def _merge_into_single_lesson(raw: dict) -> dict:
    """Fold a multi-lesson answer into the one plan the template holds.

    Models often read "4 classroom sessions" as "return 4 lessons". That answer is not wrong,
    just split, so merging preserves its coverage instead of discarding the work and asking again.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("lessons"), list):
        return raw
    lessons = [
        lesson for lesson in raw["lessons"]
        if isinstance(lesson, dict) and str(lesson.get("title", "")).strip()
    ]
    if len(lessons) <= 1:
        return raw

    merged = dict(lessons[0])
    for field, limit in MERGE_ITEM_LIMITS.items():
        combined: list[str] = []
        seen: set[str] = set()
        for lesson in lessons:
            for item in lesson.get(field, []) or []:
                text = str(item).strip()
                key = " ".join(text.lower().split())
                if text and key not in seen:
                    seen.add(key)
                    combined.append(text)
        merged[field] = combined[:limit]

    topics: list[str] = []
    seen_topics: set[str] = set()
    for lesson in lessons:
        for topic in lesson.get("source_topics", []) or []:
            key = " ".join(str(topic).lower().split())
            if key and key not in seen_topics:
                seen_topics.add(key)
                topics.append(str(topic))
    merged["source_topics"] = topics

    pages = [int(lesson.get("start_page") or 1) for lesson in lessons]
    ends = [int(lesson.get("end_page") or 1) for lesson in lessons]
    merged["start_page"] = min(pages)
    merged["end_page"] = max(ends)
    merged["reflection"] = next(
        (str(lesson.get("reflection", "")).strip() for lesson in lessons if str(lesson.get("reflection", "")).strip()),
        "",
    )
    images: list[int] = []
    for lesson in lessons:
        for page in lesson.get("recommended_image_pages", []) or []:
            if str(page).isdigit() and int(page) not in images:
                images.append(int(page))
    merged["recommended_image_pages"] = images

    result = dict(raw)
    result["lessons"] = [merged]
    return result


def _drop_chunk_scope_warnings(result: dict) -> list[str]:
    """Each chunk only sees part of the document, so a chunk complaining that a topic's content
    is absent is describing its own slice. Drop the warning when the merged analysis does in fact
    cover that topic, so the teacher is not told their document is incomplete when it is not."""
    covered = {
        _normalized_topic_title(topic.get("title", ""))
        for topic in list(result.get("topics", [])) + list(result.get("topic_candidates", []))
        if str(topic.get("summary", "")).strip()
    }
    # Very short titles would match unrelated prose, so they never justify hiding a warning.
    covered = {title for title in covered if len(title) >= 5}
    kept = []
    for warning in result.get("warnings", []):
        text = str(warning)
        lowered = text.lower()
        claims_absence = any(
            phrase in lowered
            for phrase in ("not present", "not supplied", "were supplied", "not included", "is missing")
        )
        # Compare on the same normalized form as the titles, so punctuation and numbering
        # in either the warning or the title cannot cause a mismatch.
        normalized_warning = _normalized_topic_title(text)
        if claims_absence and any(title in normalized_warning for title in covered):
            continue
        kept.append(text)
    return kept


def analyze_module(extraction: PdfExtraction, provider: ProviderConfig, options: GenerationOptions) -> dict:
    chunks = chunk_pages(extraction.pages)
    partials: list[dict] = []
    for pages in chunks:
        partials.append(
            generate_json(
                provider,
                ANALYSIS_SYSTEM,
                analysis_prompt(extraction, pages, options),
                validate=validate_module_analysis,
            )
        )

    if len(partials) == 1:
        result = validate_module_analysis(partials[0])
    else:
        merged = generate_json(
            provider, ANALYSIS_SYSTEM, merge_analysis_prompt(partials), validate=validate_module_analysis
        )
        result = validate_module_analysis(merged)
        result["warnings"] = _drop_chunk_scope_warnings(result)

    _validate_and_complete_topic_pool(result, extraction)

    result["source_filename"] = extraction.filename
    result["page_count"] = extraction.page_count
    result["source_type"] = extraction.source_type
    result["selected_start_page"] = extraction.selected_start_page
    result["selected_end_page"] = extraction.selected_end_page or extraction.page_count
    result["selected_page_count"] = len(extraction.pages)
    result["source_format"] = extraction.source_format
    result["location_kind"] = extraction.location_kind
    if extraction.source_format == "docx" and extraction.images:
        # Branding and unusable figures are extracted for download but never offered to the model.
        result["image_catalog"] = [
            {
                "xref": int(image["xref"]),
                "document_part": int(image.get("page", 1)),
                "nearby_text": str(image.get("context", ""))[:700],
            }
            for image in extraction.images
            if image.get("lesson_candidate", True)
        ]
    result["local_heading_candidates"] = [
        {"page": item.page, "text": item.text, "score": item.score}
        for item in extraction.heading_candidates
    ]
    result["warnings"] = list(dict.fromkeys(extraction.warnings + result.get("warnings", [])))
    return result


def generate_lessons(
    analysis: dict,
    settings: PlanningSettings,
    provider: ProviderConfig,
    options: GenerationOptions,
) -> dict:
    planning_analysis = dict(analysis)
    planning_analysis.pop("topic_candidates", None)
    prompt = lesson_generation_prompt(planning_analysis, settings, options)

    def _usable_plan(candidate: dict) -> None:
        """Reject only a model that cannot produce the plan SHAPE, so routing moves on.

        A plan that is merely thin - a section short of its minimum, or missing topic coverage -
        is repairable by the one-time repair call below, which is far cheaper than restarting on
        another model. Discarding a nearly-correct answer would waste the work already done.
        """
        if not isinstance(candidate, dict) or not isinstance(candidate.get("lessons"), list):
            raise ValidationError("Lesson generation did not return a lessons array.")
        titled = [
            lesson for lesson in candidate["lessons"]
            if isinstance(lesson, dict) and str(lesson.get("title", "")).strip()
        ]
        if not titled:
            raise ValidationError("Lesson generation returned no titled lesson.")

    raw = generate_json(provider, LESSON_SYSTEM, prompt, validate=_usable_plan)
    raw = _merge_into_single_lesson(raw)
    raw["minutes_per_lesson"] = settings.minutes_per_lesson
    try:
        result = validate_lesson_plan(raw, 1)
        _validate_plan_topic_coverage(result, planning_analysis)
    except ValidationError as first_error:
        repair_prompt = (
            prompt
            + "\n\nYour previous response failed validation: "
            + str(first_error)
            + "\nReturn a complete corrected JSON object with exactly the requested lesson count."
        )
        raw = generate_json(provider, LESSON_SYSTEM, repair_prompt)
        raw = _merge_into_single_lesson(raw)
        raw["minutes_per_lesson"] = settings.minutes_per_lesson
        result = validate_lesson_plan(raw, 1)
        # The repair already had its chance. Losing the whole plan over coverage the teacher can
        # see and edit themselves helps nobody, so record what looks missing and hand the plan over.
        try:
            _validate_plan_topic_coverage(result, planning_analysis)
        except ValidationError as second_error:
            result["coverage_warning"] = str(second_error)
    result["settings"] = settings.to_dict()
    result["module_title"] = analysis.get("module_title", "Untitled module")
    result["course_title"] = analysis.get("course_title", "")
    result["source_format"] = analysis.get("source_format", "pdf")
    result["options"] = options.to_dict()
    return result


def _validate_plan_topic_coverage(plan: dict, analysis: dict) -> None:
    expected_items = list(analysis.get("topics", [])) + list(analysis.get("emphasized_topics", []))
    expected_titles = [str(item.get("title", "")).strip() for item in expected_items if str(item.get("title", "")).strip()]
    if not expected_titles:
        return
    actual_titles = [
        str(title).strip()
        for lesson in plan.get("lessons", [])
        for title in lesson.get("source_topics", [])
        if str(title).strip()
    ]
    actual_keys = [_normalized_topic_title(title) for title in actual_titles]
    missing = [title for title in expected_titles if _normalized_topic_title(title) not in actual_keys]
    if missing:
        raise ValidationError("The plan omitted selected coverage: " + ", ".join(missing) + ".")
    expected_keys = [_normalized_topic_title(title) for title in expected_titles]
    positions = [actual_keys.index(key) for key in expected_keys]
    if positions != sorted(positions):
        # Coverage is complete; normalize harmless model metadata reordering
        # instead of spending another provider request or failing the workflow.
        if len(plan.get("lessons", [])) == 1:
            plan["lessons"][0]["source_topics"] = expected_titles
            plan["coverage_order_normalized"] = True
        else:
            raise ValidationError("The plan did not preserve the selected topic order.")


def _validate_and_complete_topic_pool(result: dict, extraction: PdfExtraction) -> None:
    allowed_start = min(page.number for page in extraction.pages)
    allowed_end = max(page.number for page in extraction.pages)
    warnings = result.setdefault("warnings", [])

    def in_scope(topic: dict) -> bool:
        return allowed_start <= int(topic.get("start_page", 0)) <= int(topic.get("end_page", 0)) <= allowed_end

    selected = [topic for topic in result.get("topics", []) if in_scope(topic)]
    if len(selected) != len(result.get("topics", [])):
        warnings.append("One or more model topics outside the selected source range were rejected.")
    substantive_selected = [topic for topic in selected if not _is_non_topic_section(topic)]
    if substantive_selected and len(substantive_selected) != len(selected):
        warnings.append("Generic introductions, activities, and support sections were kept under their parent topics rather than promoted to topic cards.")
        selected = substantive_selected
    if not selected:
        raise ValidationError("No model topic was supported by the selected source range.")
    result["topics"] = selected

    seen_titles = {_normalized_topic_title(topic.get("title", "")) for topic in selected}
    module_title = _normalized_topic_title(result.get("module_title", ""))
    course_title = _normalized_topic_title(result.get("course_title", ""))
    module_number = _normalized_topic_title(result.get("module_number", ""))
    seen_titles.update(title for title in (module_title, course_title) if title)
    if module_number and module_title:
        seen_titles.add(f"{module_number} {module_title}")
    candidates: list[dict] = []
    for candidate in result.get("topic_candidates", []):
        title_key = _normalized_topic_title(candidate.get("title", ""))
        if not title_key or title_key in seen_titles or not in_scope(candidate) or _is_non_topic_section(candidate):
            continue
        seen_titles.add(title_key)
        if candidate.get("content_role") == "subtopic" and not candidate.get("parent_title"):
            candidate["parent_title"] = _parent_title_for_heading(str(candidate.get("title", "")), selected, extraction.heading_candidates)
        candidates.append(candidate)

    pages_by_number = {page.number: page.text for page in extraction.pages}
    for heading in extraction.heading_candidates:
        if len(candidates) >= 60:
            break
        if heading.score < 2.8 or not allowed_start <= heading.page <= allowed_end:
            continue
        # A heading lifted straight from a contents page carries its dot leader and page number
        # ("2.1 Drawing Boards ....... 3"). Clean it the same way model titles are cleaned.
        heading_title = _clean_topic_title(heading.text)
        title_key = _normalized_topic_title(heading_title)
        if not title_key or title_key in seen_titles or _is_generic_heading(title_key):
            continue
        source_number = _source_heading_number(heading_title)
        is_subtopic = bool(source_number and "." in source_number)
        seen_titles.add(title_key)
        candidates.append(
            {
                "title": heading_title,
                "title_inferred": False,
                "start_page": heading.page,
                "end_page": heading.page,
                "summary": _heading_context_summary(pages_by_number.get(heading.page, ""), heading.text),
                "subtopics": [],
                "key_concepts": [],
                "practical_tasks": [],
                "resources": [],
                "selection_reason": "Additional explicit instructional subtopic detected in the selected source."
                if is_subtopic else "Additional explicit heading detected in the selected source.",
                "content_role": "subtopic" if is_subtopic else "topic",
                "parent_title": _parent_title_for_heading(heading_title, selected, extraction.heading_candidates)
                if is_subtopic else "",
                "confidence": "high",
            }
        )
    candidates.sort(key=lambda item: (item["start_page"], item["end_page"], item["title"]))
    result["topic_candidates"] = candidates


def _normalized_topic_title(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
    return re.sub(r"^\d+(?:\s+\d+)*\s+", "", normalized)


def _is_generic_heading(value: str) -> bool:
    return _is_non_topic_section({"title": value})


def _is_non_topic_section(topic: dict) -> bool:
    role = str(topic.get("content_role", "")).strip().lower()
    if role in {"activity", "introduction", "reference", "resource", "notes"}:
        return True
    title = _normalized_topic_title(topic.get("title", ""))
    if title in {
        "introduction", "overview", "contents", "module contents", "objectives", "course objectives",
        "module objectives",
        "references", "resources", "supplementary resources", "supplementary recourses", "student notes",
        "students notes", "notes", "table of contents",
    }:
        return True
    if title.endswith("development unit") or title.startswith("prepared by"):
        return True
    if "curriculum development unit" in title or "copyright" in title:
        return True
    return bool(re.match(r"^(?:activity|exercise)\s+\d+(?:\s+\d+)*$", title))


def _heading_context_summary(page_text: str, heading: str) -> str:
    normalized_text = " ".join(str(page_text).split())
    position = normalized_text.lower().find(" ".join(str(heading).lower().split()))
    if position >= 0:
        snippet = normalized_text[position + len(heading): position + len(heading) + 260].strip(" :-–—")
    else:
        snippet = normalized_text[:260]
    return snippet or "Explicit source section available for teacher review."


def _source_heading_number(value: str) -> str:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)[.):]?\s+", str(value))
    return match.group(1) if match else ""


def _parent_title_for_heading(title: str, selected: list[dict], headings: list) -> str:
    number = _source_heading_number(title)
    if not number or "." not in number:
        return ""
    root = number.split(".", 1)[0]
    parent_heading = next(
        (heading for heading in headings if _source_heading_number(heading.text) == root),
        None,
    )
    if parent_heading is None:
        return ""
    parent_key = _normalized_topic_title(parent_heading.text)
    for topic in selected:
        if _normalized_topic_title(topic.get("title", "")) == parent_key:
            return str(topic.get("title", ""))
    return ""


def rewrite_lesson_text(text: str, instruction: str, context: dict, provider: ProviderConfig) -> str:
    selected = str(text).strip()
    request = str(instruction).strip()
    if not selected:
        raise ValidationError("Select a non-empty paragraph to rewrite.")
    if not request:
        raise ValidationError("Enter a rewrite instruction.")
    raw = generate_json(provider, REWRITE_SYSTEM, rewrite_prompt(selected, request, context))
    revised = str(raw.get("text", "")).strip()
    if not revised:
        raise ValidationError("The model returned an empty rewrite.")
    return revised


def suggest_lesson_rewrites(
    text: str,
    instruction: str,
    field: str,
    lesson: dict,
    source_context: dict,
    excluded: list[str],
    provider: ProviderConfig,
) -> list[str]:
    selected = str(text).strip()
    if not selected:
        raise ValidationError("Select a non-empty paragraph before requesting suggestions.")
    raw = generate_json(
        provider,
        REWRITE_SUGGESTION_SYSTEM,
        rewrite_suggestions_prompt(selected, instruction, field, lesson, source_context, excluded),
    )
    values = raw.get("suggestions", [])
    if not isinstance(values, list):
        raise ValidationError("The model did not return a suggestions list.")
    blocked = {" ".join(str(value).lower().split()) for value in [selected, *excluded] if str(value).strip()}
    suggestions: list[str] = []
    for value in values:
        suggestion = " ".join(str(value).split()).strip()
        key = suggestion.lower()
        if not suggestion or key in blocked:
            continue
        blocked.add(key)
        suggestions.append(suggestion)
        if len(suggestions) == 5:
            break
    if not suggestions:
        raise ValidationError("The model did not return any new usable suggestions.")
    return suggestions


def revise_lesson_section(
    field: str,
    items: list[str],
    instruction: str,
    item_limit: int,
    lesson: dict,
    source_context: dict,
    provider: ProviderConfig,
) -> tuple[list[str], str]:
    request = str(instruction).strip()
    if not request:
        raise ValidationError("Tell the AI what to change in this section.")
    current = [" ".join(str(item).split()).strip() for item in items if str(item).strip()]
    raw = generate_json(
        provider,
        SECTION_REVISION_SYSTEM,
        section_revision_prompt(field, current, request, item_limit, lesson, source_context),
    )
    values = raw.get("items", [])
    if not isinstance(values, list):
        raise ValidationError("The model did not return a revised section list.")
    revised: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = " ".join(str(value).split()).strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        revised.append(item)
    if not revised:
        raise ValidationError("The model returned an empty section.")
    # The teacher decides how many points a section carries. item_limit is what the template
    # holds comfortably, so it guides the model and warns afterwards, but never truncates:
    # the two-page render check is the real gate before any download.
    note = str(raw.get("note", "")).strip()
    if len(revised) > item_limit:
        overflow = (
            f"This section now has {len(revised)} points; the template fits about {item_limit} "
            "comfortably, so check the two-page preview before downloading."
        )
        note = f"{note} {overflow}".strip() if note else overflow
    return revised, note


def suggest_image_page(
    section_name: str,
    section_text: list[str],
    pages: list[dict],
    provider: ProviderConfig,
) -> int | None:
    raw = generate_json(
        provider,
        IMAGE_SELECTION_SYSTEM,
        image_selection_prompt(section_name, section_text, pages),
    )
    value = raw.get("page")
    if value is None:
        return None
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("The model did not return a valid image page.") from exc
    available = {int(item["page"]) for item in pages}
    if page not in available:
        raise ValidationError("The model selected an unavailable image page.")
    return page


def suggest_word_image_xref(
    section_name: str,
    section_text: list[str],
    images: list[dict],
    provider: ProviderConfig,
) -> int | None:
    raw = generate_json(
        provider,
        IMAGE_SELECTION_SYSTEM,
        word_image_selection_prompt(section_name, section_text, images),
    )
    value = raw.get("xref")
    if value is None:
        return None
    try:
        xref = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("The model did not return a valid source image.") from exc
    available = {int(item["xref"]) for item in images}
    if xref not in available:
        raise ValidationError("The model selected an unavailable source image.")
    return xref
