from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


class ValidationError(ValueError):
    """Raised when a model response does not match the planner contract."""


@dataclass(slots=True)
class SourceSettings:
    source_type: str = "module"
    read_mode: str = "entire"
    page_from: int | None = None
    page_to: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SourceSettings":
        raw = raw or {}
        source_type = str(raw.get("source_type", "module")).strip().lower()
        read_mode = str(raw.get("read_mode", "entire")).strip().lower()
        if source_type not in {"module", "book"}:
            raise ValidationError("PDF type must be a teacher module or a book/other PDF.")
        if read_mode not in {"entire", "range"}:
            raise ValidationError("Choose whether to read the entire document or a PDF page range.")
        if read_mode == "entire":
            return cls(source_type=source_type, read_mode=read_mode)
        try:
            page_from = int(raw["page_from"])
            page_to = int(raw["page_to"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Enter both From and To physical PDF page numbers.") from exc
        if page_from < 1 or page_to < 1:
            raise ValidationError("PDF page numbers must start at 1.")
        if page_to < page_from:
            raise ValidationError("The To page must be the same as or later than the From page.")
        return cls(source_type=source_type, read_mode=read_mode, page_from=page_from, page_to=page_to)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlanningSettings:
    module_weeks: int
    minutes_per_lesson: int
    lessons_per_week: int
    theory_percent: int
    practical_percent: int
    starting_date: str
    student_level: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlanningSettings":
        try:
            settings = cls(
                module_weeks=int(raw.get("module_weeks", raw.get("number_of_lessons", 1))),
                minutes_per_lesson=int(raw["minutes_per_lesson"]),
                lessons_per_week=int(raw["lessons_per_week"]),
                theory_percent=int(raw["theory_percent"]),
                practical_percent=int(raw["practical_percent"]),
                starting_date=str(raw["starting_date"]).strip(),
                student_level=str(raw["student_level"]).strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("All six planning settings are required.") from exc

        if not 1 <= settings.module_weeks <= 20:
            raise ValidationError("Module duration must be between 1 and 20 weeks.")
        if not 20 <= settings.minutes_per_lesson <= 300:
            raise ValidationError("Minutes per lesson must be between 20 and 300.")
        if not 1 <= settings.lessons_per_week <= 14:
            raise ValidationError("Lessons per week must be between 1 and 14.")
        if settings.theory_percent + settings.practical_percent != 100:
            raise ValidationError("Theory and practical percentages must total 100.")
        if not settings.student_level:
            raise ValidationError("Student level/class is required.")
        try:
            date.fromisoformat(settings.starting_date)
        except ValueError as exc:
            raise ValidationError("Starting date must be a valid date.") from exc
        return settings

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["total_sessions"] = self.module_weeks * self.lessons_per_week
        return result

    @property
    def total_sessions(self) -> int:
        return self.module_weeks * self.lessons_per_week


@dataclass(slots=True)
class GenerationOptions:
    allow_inferred_titles: bool = True
    extract_images: bool = False
    max_images_per_lesson: int = 1
    image_placement: str = "focused_instruction"
    include_practical_tasks: bool = True
    include_home_learning: bool = True
    include_reflection: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "GenerationOptions":
        raw = raw or {}
        option = cls(
            allow_inferred_titles=_bool(raw.get("allow_inferred_titles"), True),
            extract_images=_bool(raw.get("extract_images"), False),
            max_images_per_lesson=int(raw.get("max_images_per_lesson", 1)),
            image_placement=str(raw.get("image_placement", "focused_instruction")),
            include_practical_tasks=_bool(raw.get("include_practical_tasks"), True),
            include_home_learning=_bool(raw.get("include_home_learning"), True),
            include_reflection=_bool(raw.get("include_reflection"), True),
        )
        if not 0 <= option.max_images_per_lesson <= 3:
            raise ValidationError("Images per lesson must be between 0 and 3.")
        if option.image_placement not in {"automatic", "focused_instruction", "guided_instruction"}:
            raise ValidationError("Image placement must be automatic, focused, or guided instruction.")
        return option

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list.")
    return [str(item).strip() for item in value if str(item).strip()]


def validate_module_analysis(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("Module analysis must be a JSON object.")

    topics_raw = raw.get("topics")
    if not isinstance(topics_raw, list) or not topics_raw:
        raise ValidationError("The model did not identify any teachable topics.")

    topics = _normalize_analysis_topics(topics_raw, "topics")

    if not topics:
        raise ValidationError("The model returned topics without usable titles.")

    candidates_raw = raw.get("topic_candidates", raw.get("additional_topics", []))
    topic_candidates = _normalize_analysis_topics(candidates_raw, "topic_candidates") if isinstance(candidates_raw, list) else []
    selected_keys = {_topic_key(topic) for topic in topics}
    unique_candidates: list[dict[str, Any]] = []
    candidate_keys = set(selected_keys)
    for candidate in topic_candidates:
        key = _topic_key(candidate)
        if key in candidate_keys:
            continue
        candidate_keys.add(key)
        unique_candidates.append(candidate)

    return {
        "module_title": str(raw.get("module_title") or "Untitled module").strip(),
        "course_title": str(raw.get("course_title") or "").strip(),
        "module_number": str(raw.get("module_number") or "").strip(),
        "objectives": _strings(raw.get("objectives", []), "objectives"),
        "topics": topics,
        "topic_candidates": unique_candidates,
        "warnings": _strings(raw.get("warnings", []), "warnings"),
    }


# A page footer such as "2 | P a g e" is drawn with letter spacing, so it reaches the heading
# detector as a run of single letters and used to be glued onto the real heading beside it.
_PAGE_FOOTER = re.compile(r"^\s*\d+\s*[|/]?\s*P\s*a\s*g\s*e\s*[|/]?\s*", re.IGNORECASE)


def _clean_topic_title(value: Any) -> str:
    """Strip table-of-contents decoration from a title.

    A contents line arrives as "2.7 Drawing pencils ............ 5". The subject is real, but the
    dot leader and trailing page number are not part of it and made coverage messages unreadable.
    """
    title = " ".join(str(value).split())
    title = _PAGE_FOOTER.sub("", title)                          # "2 | P a g e Module 1"
    title = re.sub(r"[.\u2026]{3,}\s*\d*\s*$", "", title)      # leader plus trailing page number
    title = re.sub(r"[.\u2026]{3,}", " ", title)                 # a leader in the middle
    title = re.sub(r"\s+[.\u2026]+\s*$", "", title)
    return " ".join(title.split()).strip(" .\u2026-")


def _normalize_analysis_topics(raw_topics: list[Any], field_name: str) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for index, topic in enumerate(raw_topics, start=1):
        if not isinstance(topic, dict):
            continue
        title = _clean_topic_title(topic.get("title", ""))
        if not title:
            continue
        try:
            start_page = max(1, int(topic.get("start_page") or 1))
            end_page = max(start_page, int(topic.get("end_page") or start_page))
        except (TypeError, ValueError):
            start_page = end_page = 1
        topics.append(
            {
                "title": title,
                "title_inferred": bool(topic.get("title_inferred", False)),
                "start_page": start_page,
                "end_page": end_page,
                "summary": str(topic.get("summary", "")).strip(),
                "subtopics": _strings(topic.get("subtopics", []), f"{field_name}[{index}].subtopics"),
                "key_concepts": _strings(topic.get("key_concepts", []), f"{field_name}[{index}].key_concepts"),
                "practical_tasks": _strings(topic.get("practical_tasks", []), f"{field_name}[{index}].practical_tasks"),
                "resources": _strings(topic.get("resources", []), f"{field_name}[{index}].resources"),
                "selection_reason": str(topic.get("selection_reason", topic.get("why_useful", ""))).strip(),
                "content_role": str(topic.get("content_role", "topic")).strip().lower()
                if str(topic.get("content_role", "topic")).strip().lower()
                in {"topic", "subtopic", "activity", "introduction", "reference", "resource", "notes"}
                else "topic",
                "parent_title": str(topic.get("parent_title", "")).strip(),
                "confidence": str(topic.get("confidence", "high")).strip().lower()
                if str(topic.get("confidence", "high")).strip().lower() in {"high", "medium"}
                else "medium",
            }
        )
    topics.sort(key=lambda item: (item["start_page"], item["end_page"]))
    return topics


def _topic_key(topic: dict[str, Any]) -> tuple[str, int, int]:
    title = " ".join(str(topic.get("title", "")).lower().split())
    return title, int(topic.get("start_page", 1)), int(topic.get("end_page", 1))


LESSON_LIST_FIELDS = (
    "resources",
    "kpis",
    "c21_skills",
    "success_criteria",
    "cross_curricular",
    "warm_up",
    "focused_instruction",
    "guided_instruction",
    "collaborative_learning",
    "independent_learning",
    "progress_check",
    "home_learning",
)


def validate_lesson_plan(raw: Any, expected_count: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("lessons"), list):
        raise ValidationError("Lesson generation did not return a lessons array.")

    lessons: list[dict[str, Any]] = []
    for index, lesson in enumerate(raw["lessons"], start=1):
        if not isinstance(lesson, dict):
            continue
        title = str(lesson.get("title", "")).strip()
        if not title:
            continue
        item: dict[str, Any] = {
            "lesson_number": index,
            "title": title,
            "page_range": str(lesson.get("page_range", "")).strip(),
            "source_topics": _strings(lesson.get("source_topics", []), f"lessons[{index}].source_topics"),
            "lesson_type": str(lesson.get("lesson_type", "Blended")).strip() or "Blended",
            "reflection": str(lesson.get("reflection", "")).strip(),
            "recommended_image_stage": str(lesson.get("recommended_image_stage", "focused_instruction")).strip(),
        }
        if item["recommended_image_stage"] not in {"focused_instruction", "guided_instruction"}:
            item["recommended_image_stage"] = "focused_instruction"
        try:
            item["start_page"] = max(1, int(lesson.get("start_page") or 1))
            item["end_page"] = max(item["start_page"], int(lesson.get("end_page") or item["start_page"]))
        except (TypeError, ValueError):
            item["start_page"] = item["end_page"] = 1
        item["recommended_image_pages"] = [
            int(page) for page in lesson.get("recommended_image_pages", [])
            if str(page).isdigit() and int(page) >= 1
        ]
        item["recommended_image_xrefs"] = [
            int(xref) for xref in lesson.get("recommended_image_xrefs", [])
            if str(xref).isdigit() and int(xref) >= 1
        ]
        for field in LESSON_LIST_FIELDS:
            item[field] = _strings(lesson.get(field, []), f"lessons[{index}].{field}")
        minimum_overview_items = {
            "kpis": 3,
            "c21_skills": 3,
            "success_criteria": 3,
            "cross_curricular": 2,
        }
        # Every teaching stage the teacher will actually deliver needs at least one activity.
        # Without this a model could return empty stages and the document came out half blank.
        minimum_overview_items.update({
            "warm_up": 1,
            "focused_instruction": 1,
            "guided_instruction": 1,
            "collaborative_learning": 1,
            "independent_learning": 1,
            "progress_check": 1,
        })
        missing = [
            f"{field} needs at least {minimum}"
            for field, minimum in minimum_overview_items.items()
            if len(item[field]) < minimum
        ]
        if missing:
            raise ValidationError(
                f"Lesson {index} left required sections empty: " + "; ".join(missing) + "."
            )
        item["timings"] = normalize_timings(lesson.get("timings", {}), int(raw.get("minutes_per_lesson") or 100))
        lessons.append(item)

    if len(lessons) != expected_count:
        raise ValidationError(f"Expected {expected_count} lessons, but the model returned {len(lessons)}.")
    return {"lessons": lessons}


def normalize_timings(raw: Any, total: int) -> dict[str, int]:
    fields = ["warm_up", "focused", "guided", "collaborative", "independent", "progress_check"]
    if isinstance(raw, dict):
        try:
            timings = {field: max(0, int(raw.get(field, 0))) for field in fields}
        except (TypeError, ValueError):
            timings = {}
        if timings and sum(timings.values()) == total and all(timings.values()):
            return timings

    weights = [0.10, 0.20, 0.20, 0.20, 0.20, 0.10]
    values = [max(1, round(total * weight)) for weight in weights]
    values[-1] += total - sum(values)
    return dict(zip(fields, values, strict=True))
