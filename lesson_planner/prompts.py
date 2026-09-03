from __future__ import annotations

import json

from .pdf_reader import PdfExtraction, PdfPage, format_pages
from .schemas import GenerationOptions, PlanningSettings


ANALYSIS_SYSTEM = """You are a curriculum document analyst. Reconstruct the real teaching hierarchy of an uploaded teacher module, book, or other teaching document. The source may be PDF or Word and can use any structure, language conventions, numbering scheme, or lack a table of contents. When a table of contents or module-contents table exists, treat it as primary evidence for top-level instructional topics. Generic labels such as Introduction or Overview belong to the following topic unless they have a specific descriptive subject. Numbered Activity or Exercise labels are activities under a parent topic, not topic titles. Resources, supplementary resources, references, contents, objectives, notes, repeated headers, footers, page numbers, and copyright lines are not teachable topic titles. Preserve explicit subject titles exactly when they are clear. If no subject title exists for a coherent section, create a concise descriptive title and mark title_inferred true. Never assume the source resembles an example seen previously. Return JSON only."""


def analysis_prompt(extraction: PdfExtraction, pages: list[PdfPage], options: GenerationOptions) -> str:
    page_numbers = {page.number for page in pages}
    candidates = [
        {"page": item.page, "text": item.text, "score": item.score}
        for item in extraction.heading_candidates
        if item.page in page_numbers
    ]
    inference_rule = (
        "If no explicit title exists, create a concise descriptive title and set title_inferred true."
        if options.allow_inferred_titles
        else "Do not invent missing section titles. Use 'Untitled section (source X-Y)', set title_inferred true, and add a warning for teacher review."
    )
    source_label = "teacher module" if extraction.source_type == "module" else "book or other teaching document"
    is_pdf = extraction.source_format == "pdf"
    boundary_rule = (
        "The selected range begins after physical page 1 and may start mid-chapter. Infer a concise, reviewable title from the selected content when an explicit title is outside the range. Do not pull in or assume content from unselected pages."
        if is_pdf and extraction.selected_start_page > 1
        else "Analyze only the supplied selected pages. Do not assume content from pages outside the selection."
        if is_pdf
        else "Word document parts are extraction boundaries, not rendered Word page numbers. Analyze only the supplied parts and cite their part numbers as internal source locations."
    )
    location = (
        f"physical PDF pages {pages[0].number}-{pages[-1].number}"
        if is_pdf
        else f"Word document parts {pages[0].number}-{pages[-1].number}"
    )
    marker = "PAGE" if is_pdf else "DOCUMENT PART"
    return f"""Analyze {location} of {extraction.filename}.
Source kind: {source_label}.
Selection boundary: {boundary_rule}

Title rule: {inference_rule}

Layout-derived heading candidates (evidence, not guaranteed headings):
{json.dumps(candidates, ensure_ascii=False)}

Page text:
{format_pages(pages, marker)}

Return this exact object shape:
{{
  "module_title": "best explicit title for this selected teaching material, or a concise inferred selection title",
  "course_title": "course/program title, or empty",
  "module_number": "number/identifier, or empty",
  "objectives": ["objective stated by the source"],
  "topics": [
    {{
      "title": "explicit or carefully inferred teachable section title",
      "title_inferred": false,
      "content_role": "topic | subtopic",
      "parent_title": "explicit parent topic title, or empty for a top-level topic",
      "start_page": 1,
      "end_page": 1,
      "summary": "faithful two-sentence content summary",
      "subtopics": ["subheading or coherent subtopic"],
      "key_concepts": ["specific fact, process, formula, or skill"],
      "practical_tasks": ["source practical activity or procedure"],
      "resources": ["equipment, software, material, or figure explicitly needed"]
    }}
  ],
  "topic_candidates": [
    {{
      "title": "another explicit or carefully inferred teachable section not already in topics",
      "title_inferred": false,
      "content_role": "topic | subtopic",
      "parent_title": "explicit parent topic title, or empty for a top-level topic",
      "start_page": 1,
      "end_page": 1,
      "summary": "faithful description that lets a teacher decide whether to add it",
      "subtopics": ["source-backed subheading"],
      "key_concepts": ["source-backed concept"],
      "practical_tasks": ["source practical task"],
      "resources": ["source resource"],
      "selection_reason": "why this is optional or narrower than the recommended topics",
      "confidence": "high | medium"
    }}
  ],
  "warnings": ["ambiguity, missing text, or section boundary issue"]
}}

Put the strongest essential subject sections in topics. Put other genuine teachable subject sections and narrower content subtopics in topic_candidates so the teacher can add them later. Never put a generic Introduction/Overview label, Activity/Exercise number, figure caption, contents entry for resources/references, objectives, or notes into either list. Store source activities inside their parent topic's practical_tasks instead. Aim for 2-8 non-duplicated candidates when the selected source contains them, but return none rather than inventing one. Every candidate title, description, and source range must be directly supported by the supplied text. Use the source location numbers shown above. Do not invent objectives, equipment, technical facts, or section boundaries."""


def merge_analysis_prompt(partials: list[dict]) -> str:
    return f"""Merge these sequential page-range analyses into one module analysis.

{json.dumps(partials, ensure_ascii=False)}

Each input analyzed only one chunk of the document, so a warning saying content was missing or "not present in the supplied text" usually just means it sat in a different chunk. Discard those. Keep only warnings that are still true for the whole document.

Remove duplicated running headers and repeated topics that cross chunk boundaries. Preserve source order and the widest correct page range. Use the first non-empty overall title/course/module number. Combine objectives without duplicates. Keep the strongest essential sections in topics and merge other truthful, non-duplicated sections into topic_candidates. Never move a candidate outside the physical page or Word-part ranges supported by the inputs. Return the same JSON object shape as the inputs, with no commentary."""


LESSON_SYSTEM = """You are an experienced vocational lesson planner. Convert a faithfully extracted module analysis into classroom-ready lessons. Keep all technical facts grounded in the supplied analysis, but design appropriate pedagogy, questions, collaboration, individual practice, and assessment. Use measurable verbs. Do not claim that a lesson has already happened. Return JSON only."""

REWRITE_SYSTEM = """You edit one selected sentence or paragraph in a vocational lesson plan. Follow the teacher's instruction exactly, preserve the original technical meaning, do not introduce facts not present in the selected text or context, and return JSON only in the form {\"text\": \"revised text\"}."""

REWRITE_SUGGESTION_SYSTEM = """You propose five distinct replacement paragraphs for one selected vocational lesson-plan paragraph. Ground every technical fact in the supplied source analysis. You may improve pedagogy, clarity, questioning, assessment, or practicality, but never invent source facts or equipment. Follow the requested JSON shape exactly and return no commentary."""

SECTION_REVISION_SYSTEM = """You revise one complete lesson-plan section as an ordered list of concise points. Follow the teacher's instruction while grounding technical claims in the supplied source analysis. Preserve useful existing points unless the teacher asks to replace or remove them. The teacher decides how many points the section carries: give them the number they ask for and never refuse on grounds of a maximum. Return JSON only."""

IMAGE_SELECTION_SYSTEM = """You select a useful source-document image for one lesson-plan section using only the supplied candidates and nearby context. Follow the requested JSON shape exactly. Never select logos, institutional branding, covers, badges, watermarks, or decorative images. If no candidate is educationally useful, return null in the requested selection field."""


def lesson_generation_prompt(analysis: dict, settings: PlanningSettings, options: GenerationOptions) -> str:
    is_pdf = analysis.get("source_format", "pdf") == "pdf"
    source_location_rule = (
        "Use physical PDF page numbers for page_range, start_page, end_page, and image recommendations. Recommend images only inside that source-page range."
        if is_pdf
        else "Use 'document part X-Y' for page_range and internal Word document-part numbers for start_page/end_page. For images, choose only xrefs from image_catalog whose nearby_text directly supports the lesson. Never choose branding, logos, covers, or decorative images."
    )
    return f"""Create exactly ONE complete module lesson plan from this module analysis. The output must fit one copy of the supplied fixed lesson-plan template:

{json.dumps(analysis, ensure_ascii=False)}

Teacher choices:
{json.dumps(settings.to_dict(), ensure_ascii=False)}

Generation controls:
{json.dumps(options.to_dict(), ensure_ascii=False)}

Requirements:
- The module lasts {settings.module_weeks} week(s), with {settings.lessons_per_week} lesson(s) per week ({settings.total_sessions} classroom sessions total).
- Use the fixed template as the output boundary. Do not return one plan per topic, week, or session.
- Cover all meaningful module topics in source order inside this single plan.
- Treat topics as the teacher's selected ordered coverage. Treat emphasized_topics as priority subtopics already nested under a selected parent; represent every emphasized title explicitly in at least one KPI, instruction/practice activity, and progress check.
- source_topics must list every selected topic and emphasized subtopic title exactly once, in the teacher's order. Do not collapse or omit emphasized titles merely because a parent topic is also selected.
- Activities may mention Session 1, Session 2, etc. when sequencing is important, but remain concise.
- Balance the complete set approximately {settings.theory_percent}% theory and {settings.practical_percent}% practical.
- The timing fields describe one {settings.minutes_per_lesson}-minute classroom lesson framework and must total exactly {settings.minutes_per_lesson} minutes.
- Activities must suit {settings.student_level}.
- KPIs, success criteria, checks, and activities must align with one another.
- C21 skills must be specific applications such as collaboration, critical thinking, communication, creativity, digital literacy, or problem solving—not generic labels alone.
- Cross-curricular links may include numeracy, literacy, culture, heritage, safety, sustainability, or digital skills, but include only defensible links.
- Reflection is a lesson-specific editable teacher reflection prompt/anticipated recap, not a false statement that students already succeeded.
- Use concise text that fits a lesson-plan table.
- Hard capacity limits: resources max 5; KPIs max 4; C21 skills max 4; success criteria max 4; cross-curricular max 3; each teaching-stage list max 3 items; homework max 2; reflection max 45 words.
- Fill the overview evenly without padding or repetition: return 3-4 distinct KPIs, 3-4 aligned success criteria, 3-4 specific C21 skill applications, and 2-3 defensible cross-curricular links.
- Keep each list item under 22 words unless a safety-critical instruction requires slightly more.
- If include_practical_tasks is false, do not create workshop/group practical procedures; use theory-safe activities instead.
- If include_home_learning is false, return an empty home_learning array.
- If include_reflection is false, return an empty reflection string.
- Return an empty recommended_image_pages list when image extraction is disabled.
- {source_location_rule}

Return:
{{
  "minutes_per_lesson": {settings.minutes_per_lesson},
  "lessons": [
    {{
      "title": "lesson title",
      "page_range": "pages 2-4",
      "start_page": 2,
      "end_page": 4,
      "recommended_image_pages": [3],
      "recommended_image_xrefs": [],
      "recommended_image_stage": "focused_instruction",
      "source_topics": ["source topic"],
      "lesson_type": "Theory | Practical | Blended",
      "resources": ["resource"],
      "kpis": ["measurable learning focus"],
      "c21_skills": ["skill applied in this lesson"],
      "success_criteria": ["Students will be able to ..."],
      "cross_curricular": ["specific link"],
      "warm_up": ["short prior-knowledge question/activity"],
      "focused_instruction": ["teacher modelling/explanation step"],
      "guided_instruction": ["supported student practice step"],
      "collaborative_learning": ["concrete group task with an outcome"],
      "independent_learning": ["concrete individual task"],
      "progress_check": ["observable question, product, or demonstration aligned to KPIs"],
      "home_learning": ["short independent follow-up"],
      "reflection": "lesson-specific recap and teacher reflection prompt",
      "timings": {{
        "warm_up": 10,
        "focused": 20,
        "guided": 20,
        "collaborative": 20,
        "independent": 20,
        "progress_check": 10
      }}
    }}
  ]
}}

Adjust the timing values to total exactly {settings.minutes_per_lesson} for every lesson."""


def rewrite_prompt(text: str, instruction: str, context: dict) -> str:
    return f"""Selected lesson-plan text:
{text}

Teacher instruction:
{instruction}

Limited context:
{json.dumps(context, ensure_ascii=False)}

Revise only the selected text. Keep it appropriate for a compact Word lesson-plan table. Return {{"text": "..."}}."""


def rewrite_suggestions_prompt(
    text: str,
    instruction: str,
    field: str,
    lesson: dict,
    source_context: dict,
    excluded: list[str],
) -> str:
    teacher_request = instruction.strip() or "Offer varied, useful alternatives that improve this paragraph while preserving its lesson-plan role."
    return f"""Create exactly five concise replacement choices for this selected paragraph.

Selected field: {field}
Current paragraph: {text}
Teacher request: {teacher_request}

Current lesson context:
{json.dumps(lesson, ensure_ascii=False)}

Grounded source context from the already-read upload:
{json.dumps(source_context, ensure_ascii=False)}

Do not repeat these current/previous choices:
{json.dumps([text, *excluded], ensure_ascii=False)}

Requirements:
- Return five meaningfully different choices, not minor word swaps.
- Each choice must be one compact paragraph under 22 words, unless the field is reflection, where up to 35 words is allowed.
- Preserve the purpose of the selected field.
- Technical claims must be supported by the source context.
- Do not invent equipment, figures, temperatures, procedures, or terminology.

Return {{"suggestions": ["choice 1", "choice 2", "choice 3", "choice 4", "choice 5"]}}."""


def section_revision_prompt(
    field: str,
    items: list[str],
    instruction: str,
    item_limit: int,
    lesson: dict,
    source_context: dict,
) -> str:
    return f"""Revise the complete selected lesson-plan section.

Section: {field}
Current ordered points ({len(items)} of them): {json.dumps(items, ensure_ascii=False)}
Teacher instruction: {instruction}

Counting, already worked out for you - do not recount:
- The section currently holds {len(items)} points.
- The template fits about {item_limit} points comfortably. This is guidance, not a limit.
- The teacher decides how many points they want. Give them the number they asked for.

Lesson context:
{json.dumps(lesson, ensure_ascii=False)}

Grounded context from the already-read source:
{json.dumps(source_context, ensure_ascii=False)}

Rules:
- Follow the teacher's request for the whole section, not merely one paragraph.
- "Add N more" always means N points IN ADDITION TO the current ones. Never count an existing point as one of the new ones. Three existing points plus "add 4 more" means return seven points.
- If asked to add points, preserve the current points verbatim and append exactly the number requested.
- NEVER refuse, and never reply with only the unchanged points. Never tell the teacher a maximum stops you.
- If the result goes past the comfortable size, still return what was asked for and simply mention in note that the two-page preview should be checked.
- Each point must be concise, distinct, and appropriate for the selected section.
- Keep each point under 22 words unless a safety-critical instruction needs slightly more.
- Do not invent source facts, equipment, figures, procedures, or technical claims.
- Return as many points as the teacher asked for.

Return {{"items": ["ordered point"], "note": "brief explanation of what changed or any capacity limit"}}."""


def image_selection_prompt(section_name: str, section_text: list[str], pages: list[dict]) -> str:
    return f"""Choose an image page for this lesson-plan section:

Section: {section_name}
Section content: {json.dumps(section_text, ensure_ascii=False)}

Available source pages containing extracted images and their nearby PDF text:
{json.dumps(pages, ensure_ascii=False)}

Choose only a page listed above. Prefer diagrams, component photos, schematics, or task figures that directly support the section. Do not choose decorative covers or logos."""


def word_image_selection_prompt(section_name: str, section_text: list[str], images: list[dict]) -> str:
    return f"""Choose one embedded Word image for this lesson-plan section:

Section: {section_name}
Section content: {json.dumps(section_text, ensure_ascii=False)}

Available educational image candidates, identified by xref and nearby document text:
{json.dumps(images, ensure_ascii=False)}

Choose only an xref listed above and only when its nearby text directly supports the section. Never choose an institution logo, branding, cover art, badge, decorative image, or unrelated photo. Return {{"xref": 3, "reason": "brief reason"}}. If none is clearly useful, return {{"xref": null, "reason": "not useful"}}."""
