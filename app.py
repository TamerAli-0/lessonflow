from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import fitz
from flask import Flask, jsonify, render_template, request, send_file

from lesson_planner.ai_providers import (
    ProviderConfig,
    ProviderError,
    list_openrouter_models,
    openrouter_auto_candidates,
)
from lesson_planner.docx_exporter import TemplateError
from lesson_planner import progress
from lesson_planner.job_store import JobStore
from lesson_planner.pdf_exporter import PdfExportError, build_pdf_hotspots, export_pdf_bundle, render_pdf_pages
from lesson_planner.planner import (
    analyze_module,
    generate_lessons,
    rewrite_lesson_text,
    revise_lesson_section,
    suggest_lesson_rewrites,
    suggest_image_page,
    suggest_word_image_xref,
)
from lesson_planner.schemas import LESSON_LIST_FIELDS, GenerationOptions, PlanningSettings, SourceSettings, ValidationError
from lesson_planner.source_reader import SUPPORTED_SOURCE_SUFFIXES, extract_source, extract_source_images


BASE_DIR = Path(__file__).resolve().parent


def _resolve_default_template() -> Path:
    """Find the Word lesson-plan template without depending on one machine's layout.

    The bundled copy in assets/ is what a fresh install uses. The environment
    variable still wins so a different template can be used, and the
    original Downloads path is kept last so existing setups keep working.
    """
    override = os.environ.get("LESSON_PLANNER_TEMPLATE")
    candidates = [Path(override)] if override else []
    candidates.append(BASE_DIR / "assets" / "lesson-plan-template.docx")
    candidates.append(Path.home() / "Downloads" / "Lesson plan template(1).docx")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_TEMPLATE_PATH = _resolve_default_template()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
# This is a local tool that is edited while it runs. Re-read templates from disk so the
# page and static/app.js can never be served from different versions of the code.
app.config["TEMPLATES_AUTO_RELOAD"] = True
store = JobStore(BASE_DIR / "runtime" / "jobs")
SECTION_ITEM_LIMITS = {
    "resources": 5,
    "kpis": 4,
    "c21_skills": 4,
    "success_criteria": 4,
    "cross_curricular": 3,
    "warm_up": 3,
    "focused_instruction": 3,
    "guided_instruction": 3,
    "collaborative_learning": 3,
    "independent_learning": 3,
    "progress_check": 3,
    "home_learning": 2,
}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze_endpoint():
    source_file = request.files.get("source") or request.files.get("module")
    if source_file is None or not source_file.filename:
        return error_response("Choose a source PDF or Word document first.", 400)
    source_suffix = Path(source_file.filename).suffix.lower()
    if source_suffix not in SUPPORTED_SOURCE_SUFFIXES:
        return error_response("Upload a PDF or Word document (.pdf, .docx, or .doc).", 400)

    run_id = str(request.form.get("run_id", ""))
    progress.set_active(run_id)
    progress.start(
        run_id,
        "Checking your settings…",
        [
            ("read", "Read the document"),
            ("images", "Collect pictures from the document"),
            ("map", "Ask the AI to map the module structure"),
            ("check", "Check the topics against your document"),
        ],
    )
    try:
        provider = ProviderConfig.from_dict(json.loads(request.form.get("provider", "{}")))
        options = GenerationOptions.from_dict(json.loads(request.form.get("options", "{}")))
        source = SourceSettings.from_dict(json.loads(request.form.get("source_settings", "{}")))
        if source_suffix != ".pdf" and source.read_mode == "range":
            raise ValidationError("Physical page ranges are available for PDFs only. Word documents are read in full.")
        job_id = store.create()
        progress.update(run_id, 8, "Saving your document…")
        source_path = store.save_upload(job_id, source_file, source_file.filename)
        progress.activate(
            run_id,
            "read",
            15,
            "Converting the Word file and reading its text…" if source_suffix == ".doc" else "Reading the document…",
        )
        extraction = extract_source(
            source_path,
            start_page=source.page_from or 1,
            end_page=source.page_to,
            source_type=source.source_type,
        )
        characters = sum(len(page.text) for page in extraction.pages)
        progress.note(
            run_id,
            "read",
            f"{characters:,} characters · {len(extraction.pages)} "
            f"{'pages' if extraction.source_format == 'pdf' else 'document parts'} · "
            f"{len(extraction.heading_candidates)} headings found",
        )
        extraction.filename = source_file.filename
        if not any(page.text for page in extraction.pages):
            if source_suffix == ".pdf":
                return error_response("The selected PDF pages contain no selectable text. OCR support will be added next.", 422)
            return error_response("This Word document contains no readable text.", 422)
        if not options.extract_images:
            progress.skip(run_id, "images", "You turned images off for this run")
        if options.extract_images:
            progress.activate(run_id, "images", 30, "Collecting pictures from the document…")
            extraction.images = extract_source_images(
                source_path,
                store.path(job_id) / "images",
                start_page=extraction.selected_start_page,
                end_page=extraction.selected_end_page,
            )
            progress.note(
                run_id,
                "images",
                f"{len(extraction.images)} usable image(s) found" if extraction.images
                else "No usable images in this document",
            )
        progress.activate(run_id, "map", 40, "Asking the AI to map the module structure. This is the longest step…")
        analysis = analyze_module(extraction, provider, options)
        progress.activate(run_id, "check", 92, "Checking the topics against your document…")
        topic_titles = [str(topic.get("title", "")) for topic in analysis.get("topics", [])]
        progress.note(
            run_id,
            "map",
            ", ".join(topic_titles[:4]) + (" …" if len(topic_titles) > 4 else ""),
            headline=f"AI mapped the module — {len(topic_titles)} topic(s)",
        )
        progress.note(
            run_id,
            "check",
            f"{len(analysis.get('topic_candidates', []))} extra source-backed topic(s) available to add",
        )
        store.save_json(job_id, "extraction", extraction.to_dict())
        store.save_json(job_id, "analysis", analysis)
        progress.finish(run_id, "Structure ready.")
        return jsonify({"job_id": job_id, "analysis": analysis, "images": public_images(job_id, extraction.images or [])})
    except (ProviderError, ValidationError, ValueError) as exc:
        progress.fail(run_id, str(exc))
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Module analysis failed")
        progress.fail(run_id, str(exc))
        return error_response(f"Module analysis failed: {exc}", 500)


@app.post("/api/generate")
def generate_endpoint():
    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("run_id", ""))
    progress.set_active(run_id)
    progress.start(
        run_id,
        "Checking your lesson settings…",
        [
            ("write", "Write the lesson plan"),
            ("cover", "Confirm your selected topics are covered"),
            ("images", "Place images into the right sections"),
        ],
    )
    try:
        provider = ProviderConfig.from_dict(payload.get("provider", {}))
        settings = PlanningSettings.from_dict(payload.get("settings", {}))
        options = GenerationOptions.from_dict(payload.get("options", {}))
        job_id = str(payload.get("job_id", ""))
        analysis = payload.get("analysis") or store.load_json(job_id, "analysis")
        progress.activate(run_id, "write", 20, "Writing the lesson plan from your selected topics. This is the longest step…")
        plan = generate_lessons(analysis, settings, provider, options)
        lessons = plan.get("lessons", [])
        progress.note(
            run_id,
            "write",
            ", ".join(str(lesson.get("title", "")) for lesson in lessons[:3]) or "",
            headline=f"Wrote {len(lessons)} lesson section set(s)",
        )
        progress.activate(run_id, "cover", 85, "Checking every selected topic is covered…")
        progress.note(
            run_id,
            "cover",
            ", ".join(str(topic) for topic in (lessons[0].get("source_topics", []) if lessons else [])[:4]),
        )
        extraction = store.load_json(job_id, "extraction")
        assign_selected_images(plan, extraction.get("images", []))
        placed = sum(len(lesson.get("selected_images", []) or []) for lesson in lessons)
        progress.activate(run_id, "images", 95, "Placing images into the right sections…")
        progress.note(
            run_id,
            "images",
            f"{placed} image(s) added to the plan" if placed else "No images needed for this plan",
        )
        store.save_json(job_id, "plan", plan)
        progress.finish(run_id, "Plan ready.")
        return jsonify({"plan": plan})
    except FileNotFoundError:
        progress.fail(run_id, "The analysis session expired.")
        return error_response("The analysis session expired. Upload the source document again.", 404)
    except (ProviderError, ValidationError, ValueError) as exc:
        progress.fail(run_id, str(exc))
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Lesson generation failed")
        progress.fail(run_id, str(exc))
        return error_response(f"Lesson generation failed: {exc}", 500)


@app.post("/api/export")
def export_endpoint():
    template = request.files.get("template")
    try:
        job_id = request.form["job_id"]
        analysis = json.loads(request.form["analysis"])
        plan = json.loads(request.form["plan"])
        extraction = store.load_json(job_id, "extraction")
        store.save_json(job_id, "plan", plan)
        template_path = resolve_template(job_id, template)
        export_dir = store.path(job_id) / "word-export"
        validation_pdf, _ = export_pdf_bundle(
            template_path,
            export_dir,
            analysis,
            plan,
            extraction.get("images", []),
        )
        # The template grows with the plan. A longer plan is the teacher's call, so the page
        # count is reported rather than used to refuse the download.
        rendered_pdf = fitz.open(validation_pdf)
        page_count = len(rendered_pdf)
        rendered_pdf.close()
        documents = sorted((export_dir / "lesson-documents").glob("*.docx"))
        if len(documents) != 1:
            raise TemplateError("Expected exactly one generated Word document.")
        return send_file(
            documents[0],
            as_attachment=True,
            download_name="generated-lesson-plan.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except (KeyError, json.JSONDecodeError, ValidationError, TemplateError, PdfExportError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("DOCX export failed")
        return error_response(f"Word export failed: {exc}", 500)


@app.post("/api/export/pdf")
def export_pdf_endpoint():
    template = request.files.get("template")
    try:
        job_id = request.form["job_id"]
        analysis = json.loads(request.form["analysis"])
        plan = json.loads(request.form["plan"])
        extraction = store.load_json(job_id, "extraction")
        store.save_json(job_id, "plan", plan)
        template_path = resolve_template(job_id, template)
        output, content_type = export_pdf_bundle(
            template_path,
            store.path(job_id) / "pdf-export",
            analysis,
            plan,
            extraction.get("images", []),
        )
        return send_file(output, as_attachment=True, download_name="generated-lesson-plans.pdf", mimetype=content_type)
    except (KeyError, json.JSONDecodeError, ValidationError, TemplateError, PdfExportError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("PDF export failed")
        return error_response(f"PDF export failed: {exc}", 500)


@app.post("/api/preview")
def preview_endpoint():
    template = request.files.get("template")
    try:
        job_id = request.form["job_id"]
        analysis = json.loads(request.form["analysis"])
        plan = json.loads(request.form["plan"])
        extraction = store.load_json(job_id, "extraction")
        store.save_json(job_id, "plan", plan)
        template_path = resolve_template(job_id, template)
        preview_dir = store.path(job_id) / "preview"
        output, _ = export_pdf_bundle(template_path, preview_dir, analysis, plan, extraction.get("images", []))
        rendered = render_pdf_pages(output, preview_dir / "rendered")
        hotspots = build_pdf_hotspots(output, plan)
        preview_pdf = fitz.open(output)
        page_sizes = [(page.rect.width, page.rect.height) for page in preview_pdf]
        preview_page_count = len(preview_pdf)
        preview_pdf.close()
        return jsonify(
            {
                "pdf_url": f"/api/jobs/{job_id}/preview/generated-lesson-plans.pdf",
                "layout_valid": True,
                "page_count": preview_page_count,
                "layout_message": "" if preview_page_count == 2 else (
                    f"This plan grew to {preview_page_count} pages. That is fine to download - "
                    "shorten dense sections if you would rather it fit 2."
                ),
                "pages": [
                    {
                        "page_number": index,
                        "lesson_index": 0,
                        "page_within_plan": index,
                        "url": f"/api/jobs/{job_id}/preview/rendered/{page.name}",
                        "hotspots": hotspots[index - 1] if index - 1 < len(hotspots) else [],
                        "width_points": page_sizes[index - 1][0],
                        "height_points": page_sizes[index - 1][1],
                    }
                    for index, page in enumerate(rendered, start=1)
                ],
            }
        )
    except (KeyError, json.JSONDecodeError, ValidationError, TemplateError, PdfExportError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Preview generation failed")
        return error_response(f"Preview generation failed: {exc}", 500)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/config")
def config_endpoint():
    return jsonify(
        {
            "default_template_available": DEFAULT_TEMPLATE_PATH.is_file(),
            "default_template_name": DEFAULT_TEMPLATE_PATH.name if DEFAULT_TEMPLATE_PATH.is_file() else "",
        }
    )


@app.get("/api/progress/<run_id>")
def progress_endpoint(run_id: str):
    state = progress.read(run_id)
    if state is None:
        return jsonify({"percent": 0, "message": "", "done": False, "failed": False, "known": False})
    return jsonify({**state, "known": True})


@app.get("/api/openrouter-models")
def openrouter_models_endpoint():
    try:
        return jsonify({"models": list_openrouter_models(), "auto": openrouter_auto_candidates()})
    except ProviderError as exc:
        return error_response(str(exc), 502)


@app.post("/api/rewrite")
def rewrite_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        provider = ProviderConfig.from_dict(payload.get("provider", {}))
        context = payload.get("context", {}) if isinstance(payload.get("context", {}), dict) else {}
        job_id = str(payload.get("job_id", ""))
        if job_id:
            analysis = payload.get("analysis") or store.load_json(job_id, "analysis")
            extraction = store.load_json(job_id, "extraction")
            lesson = payload.get("lesson", {}) if isinstance(payload.get("lesson"), dict) else {}
            context["source_context"] = build_rewrite_source_context(analysis, extraction, lesson)
        revised = rewrite_lesson_text(
            str(payload.get("text", "")),
            str(payload.get("instruction", "")),
            context,
            provider,
        )
        return jsonify({"text": revised})
    except (ProviderError, ValidationError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Paragraph rewrite failed")
        return error_response(f"Rewrite failed: {exc}", 500)


@app.post("/api/rewrite-suggestions")
def rewrite_suggestions_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        provider = ProviderConfig.from_dict(payload.get("provider", {}))
        job_id = str(payload.get("job_id", ""))
        field = str(payload.get("field", ""))
        allowed_fields = set(LESSON_LIST_FIELDS) | {"reflection", "title"}
        if field not in allowed_fields:
            raise ValidationError("Choose a valid lesson-plan section for suggestions.")
        lesson = payload.get("lesson", {}) if isinstance(payload.get("lesson"), dict) else {}
        analysis = payload.get("analysis") or store.load_json(job_id, "analysis")
        extraction = store.load_json(job_id, "extraction")
        excluded = payload.get("excluded", [])
        if not isinstance(excluded, list):
            excluded = []
        suggestions = suggest_lesson_rewrites(
            str(payload.get("text", "")),
            str(payload.get("instruction", "")),
            field,
            lesson,
            build_rewrite_source_context(analysis, extraction, lesson),
            [str(value) for value in excluded],
            provider,
        )
        return jsonify({"suggestions": suggestions})
    except FileNotFoundError:
        return error_response("The analysis session expired. Upload the source document again.", 404)
    except (ProviderError, ValidationError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Rewrite suggestions failed")
        return error_response(f"Suggestions failed: {exc}", 500)


@app.post("/api/revise-section")
def revise_section_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        provider = ProviderConfig.from_dict(payload.get("provider", {}))
        job_id = str(payload.get("job_id", ""))
        field = str(payload.get("field", ""))
        if field not in SECTION_ITEM_LIMITS:
            raise ValidationError("Choose an editable list section for the section assistant.")
        lesson = payload.get("lesson", {}) if isinstance(payload.get("lesson"), dict) else {}
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ValidationError("The current section must be a list of points.")
        analysis = payload.get("analysis") or store.load_json(job_id, "analysis")
        extraction = store.load_json(job_id, "extraction")
        revised, note = revise_lesson_section(
            field,
            [str(item) for item in items],
            str(payload.get("instruction", "")),
            SECTION_ITEM_LIMITS[field],
            lesson,
            build_rewrite_source_context(analysis, extraction, lesson),
            provider,
        )
        # `limit` is the size the fixed template holds comfortably. It is advice for the browser,
        # not a cap: the teacher decides, and the two-page render check gates the download.
        return jsonify({"items": revised, "note": note, "limit": SECTION_ITEM_LIMITS[field]})
    except FileNotFoundError:
        return error_response("The analysis session expired. Upload the source document again.", 404)
    except (ProviderError, ValidationError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Section revision failed")
        return error_response(f"Section assistant failed: {exc}", 500)


@app.post("/api/suggest-image")
def suggest_image_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        provider = ProviderConfig.from_dict(payload.get("provider", {}))
        job_id = str(payload.get("job_id", ""))
        field = str(payload.get("field", ""))
        lesson = payload.get("lesson", {}) if isinstance(payload.get("lesson"), dict) else {}
        extraction = store.load_json(job_id, "extraction")
        selected = {
            int(item.get("xref")) for item in lesson.get("selected_images", [])
            if isinstance(item, dict) and str(item.get("xref", "")).isdigit()
        }
        start_page = int(lesson.get("start_page", 1))
        end_page = int(lesson.get("end_page", extraction.get("page_count", start_page)))
        images = [
            image for image in extraction.get("images", [])
            if image.get("lesson_candidate", True)
            and (
                extraction.get("source_format") == "docx"
                or start_page <= int(image.get("page", 0)) <= end_page
            )
            and int(image.get("xref", -1)) not in selected
        ]
        if not images:
            return error_response("No unused source images are available for this section.", 422)
        section_value = lesson.get(field, [])
        section_text = section_value if isinstance(section_value, list) else [str(section_value)]
        if extraction.get("source_format") == "docx":
            image_context = [
                {
                    "xref": int(image["xref"]),
                    "document_part": int(image.get("page", 1)),
                    "nearby_text": str(image.get("context", ""))[:700],
                }
                for image in images
            ]
            xref = suggest_word_image_xref(field, section_text, image_context, provider)
            if xref is None:
                return error_response("The model did not find a useful source image for this section.", 422)
            chosen = next(image for image in images if int(image["xref"]) == xref)
            return jsonify({"image": public_images(job_id, [chosen])[0]})

        image_pages = sorted({int(image["page"]) for image in images})
        page_lookup = {int(page["number"]): page for page in extraction.get("pages", [])}
        page_context = [
            {
                "page": number,
                "text": str(page_lookup.get(number, {}).get("text", ""))[:1800],
                "image_count": sum(1 for image in images if int(image["page"]) == number),
            }
            for number in image_pages
        ]
        page = suggest_image_page(field, section_text, page_context, provider)
        if page is None:
            return error_response("The model did not find a useful source image for this section.", 422)
        candidates = [image for image in images if int(image["page"]) == page]
        candidates.sort(key=lambda image: int(image.get("area", 0)), reverse=True)
        return jsonify({"image": public_images(job_id, [candidates[0]])[0]})
    except FileNotFoundError:
        return error_response("The analysis session expired. Upload the source document again.", 404)
    except (ProviderError, ValidationError, ValueError) as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        app.logger.exception("Image suggestion failed")
        return error_response(f"Image suggestion failed: {exc}", 500)


@app.get("/api/jobs/<job_id>/images/<filename>")
def job_image(job_id: str, filename: str):
    try:
        if Path(filename).name != filename:
            return error_response("Invalid image path.", 400)
        path = store.path(job_id) / "images" / filename
        if not path.is_file():
            return error_response("Image not found.", 404)
        return send_file(path)
    except ValueError as exc:
        return error_response(str(exc), 400)


@app.get("/api/jobs/<job_id>/images.zip")
def job_images_archive(job_id: str):
    """Every extracted source image in one download, for editing in the real Word app."""
    try:
        directory = store.path(job_id) / "images"
        if not directory.is_dir():
            return error_response("This job has no extracted images.", 404)
        files = sorted(item for item in directory.iterdir() if item.is_file())
        wanted = str(request.args.get("names", "")).strip()
        if wanted:
            # Only the names the teacher ticked, matched against what is really on disk so a
            # crafted parameter can never reach outside the job's own image folder.
            chosen = {Path(name).name for name in wanted.split(",") if name.strip()}
            files = [item for item in files if item.name in chosen]
        if not files:
            return error_response("No matching images to download.", 404)
        archive = store.path(job_id) / "source-images.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in files:
                bundle.write(item, arcname=item.name)
        return send_file(
            archive,
            as_attachment=True,
            download_name="source-images.zip",
            mimetype="application/zip",
        )
    except ValueError as exc:
        return error_response(str(exc), 400)


@app.get("/api/jobs/<job_id>/preview/<path:filename>")
def job_preview_file(job_id: str, filename: str):
    try:
        root = (store.path(job_id) / "preview").resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return error_response("Preview file not found.", 404)
        return send_file(path)
    except ValueError as exc:
        return error_response(str(exc), 400)


def public_images(job_id: str, images: list[dict]) -> list[dict]:
    return [
        {
            "xref": int(image["xref"]),
            "page": int(image["page"]),
            "width": int(image["width"]),
            "height": int(image["height"]),
            "area": int(image["area"]),
            "url": f"/api/jobs/{job_id}/images/{Path(image['path']).name}",
            "lesson_candidate": bool(image.get("lesson_candidate", True)),
            "skip_reason": str(image.get("skip_reason", "")),
        }
        for image in images
    ]


def build_rewrite_source_context(analysis: dict, extraction: dict, lesson: dict) -> dict:
    requested_titles = {
        " ".join(str(title).lower().split())
        for title in lesson.get("source_topics", [])
        if str(title).strip()
    }
    topic_pool = list(analysis.get("topics", [])) + list(analysis.get("emphasized_topics", []))
    topic_pool += list(analysis.get("topic_candidates", []))
    topic_evidence = []
    for topic in topic_pool:
        title = str(topic.get("title", "")).strip()
        normalized = " ".join(title.lower().split())
        if requested_titles and normalized not in requested_titles and topic not in analysis.get("topics", []):
            continue
        topic_evidence.append(
            {
                "title": title,
                "summary": str(topic.get("summary", ""))[:700],
                "subtopics": topic.get("subtopics", [])[:12],
                "key_concepts": topic.get("key_concepts", [])[:12],
                "practical_tasks": topic.get("practical_tasks", [])[:8],
                "resources": topic.get("resources", [])[:8],
                "start_page": topic.get("start_page"),
                "end_page": topic.get("end_page"),
            }
        )

    start_page = int(lesson.get("start_page", extraction.get("selected_start_page", 1)))
    end_page = int(lesson.get("end_page", extraction.get("selected_end_page", extraction.get("page_count", start_page))))
    source_parts = []
    character_budget = 6_000
    for page in extraction.get("pages", []):
        number = int(page.get("number", 0))
        is_word = extraction.get("source_format") == "docx"
        if not is_word and not start_page <= number <= end_page:
            continue
        excerpt = str(page.get("text", ""))[: min(1_800, character_budget)]
        if excerpt:
            source_parts.append({"location": number, "text": excerpt})
            character_budget -= len(excerpt)
        if character_budget <= 0:
            break
    return {
        "source_format": extraction.get("source_format", "pdf"),
        "topics": topic_evidence[:16],
        "source_excerpts": source_parts,
    }


def assign_selected_images(plan: dict, images: list[dict]) -> None:
    options = plan.get("options", {})
    maximum = int(options.get("max_images_per_lesson", 0)) if options.get("extract_images") else 0
    for lesson in plan.get("lessons", []):
        if maximum <= 0:
            lesson["selected_image_xrefs"] = []
            lesson["selected_images"] = []
            continue
        start_page = int(lesson.get("start_page", 1))
        end_page = int(lesson.get("end_page", start_page))
        recommended = {int(page) for page in lesson.get("recommended_image_pages", [])}
        recommended_xrefs = {int(xref) for xref in lesson.get("recommended_image_xrefs", [])}
        source_format = str(plan.get("source_format", "pdf"))
        range_eligible = [
            image for image in images
            # Branding and unusable figures are downloadable but never auto-placed.
            if image.get("lesson_candidate", True)
            and (source_format == "docx" or start_page <= int(image.get("page", 0)) <= end_page)
        ]
        xref_eligible = [image for image in range_eligible if int(image.get("xref", -1)) in recommended_xrefs]
        recommended_eligible = [image for image in range_eligible if int(image.get("page", 0)) in recommended]
        if source_format == "docx":
            # Do not guess by image size for Word files. A model must recommend
            # an exact filtered image xref, otherwise the teacher chooses manually.
            eligible = xref_eligible
        else:
            eligible = xref_eligible or recommended_eligible or range_eligible
        eligible.sort(key=lambda image: int(image.get("area", 0)), reverse=True)
        selected = []
        for image in eligible:
            xref = int(image["xref"])
            if xref not in selected:
                selected.append(xref)
            if len(selected) >= 1:
                break
        lesson["selected_image_xrefs"] = selected
        placement = str(lesson.get("recommended_image_stage") or "focused_instruction")
        lesson["selected_images"] = [{"xref": xref, "field": placement} for xref in selected]


def resolve_template(job_id: str, upload):
    if upload is not None and upload.filename:
        if Path(upload.filename).suffix.lower() != ".docx":
            raise TemplateError("The uploaded template must be a .docx file.")
        return store.save_template(job_id, upload, upload.filename)
    if DEFAULT_TEMPLATE_PATH.is_file():
        return DEFAULT_TEMPLATE_PATH
    raise TemplateError("Choose the Word lesson-plan template (.docx) once before exporting.")


def error_response(message: str, status: int):
    return jsonify({"error": message}), status


if __name__ == "__main__":
    host = os.environ.get("LESSON_PLANNER_HOST", "127.0.0.1")
    port = int(os.environ.get("LESSON_PLANNER_PORT", "5050"))
    app.run(host=host, port=port, debug=False)
