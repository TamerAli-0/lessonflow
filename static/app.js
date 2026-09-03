const state = { jobId: null, analysis: null, plan: null, images: [], availableTopics: [], emphasizedTopics: [], topicLibraryOpen: false, draggedTopicIndex: null, config: {}, rewriteTarget: null, rewriteSuggestions: [], rewriteSuggestionInstruction: "", sectionProposal: null, previewSelection: null, inspectorDirty: false, previewHotspots: [], imageDrag: null, previewLayout: "large" };

const fieldLabels = {
  title: "Module topic & pages",
  resources: "Resources",
  kpis: "Main learning focus (KPIs)",
  c21_skills: "C21st skills",
  success_criteria: "Success criteria",
  cross_curricular: "Numeracy, literacy, culture & heritage",
  warm_up: "Warm up",
  focused_instruction: "Focused instruction",
  guided_instruction: "Guided instruction",
  collaborative_learning: "Collaborative learning",
  independent_learning: "Independent learning",
  progress_check: "Progress check",
  home_learning: "Home learning",
  reflection: "Reflection",
  images: "Lesson images"
};

const fieldCapacity = {
  title: 140, resources: 260, kpis: 420, c21_skills: 360,
  success_criteria: 420, cross_curricular: 280, warm_up: 220,
  focused_instruction: 420, guided_instruction: 420,
  collaborative_learning: 380, independent_learning: 340,
  progress_check: 340, home_learning: 180, reflection: 320
};

const fieldItemLimits = {
  resources: 5, kpis: 4, c21_skills: 4, success_criteria: 4, cross_curricular: 3,
  warm_up: 3, focused_instruction: 3, guided_instruction: 3,
  collaborative_learning: 3, independent_learning: 3, progress_check: 3, home_learning: 2
};

const imageCapableFields = new Set([
  "warm_up", "focused_instruction", "guided_instruction",
  "collaborative_learning", "independent_learning", "progress_check"
]);

const providerModels = {
  gemini: [
    ["gemini-3.6-flash", "Recommended — Gemini 3.6 Flash", "Reliable balance of quality and availability."],
    ["gemini-3.7-flash", "Best quality — Gemini 3.7 Flash", "Most capable, but can be busy during demand spikes."],
    ["gemini-3.5-flash-lite", "Fastest — Gemini 3.5 Flash-Lite", "Best when you want speed and lower demand."],
    ["gemini-2.5-flash", "Older stable — Gemini 2.5 Flash", "Useful fallback if newer models are unavailable."]
  ],
  groq: [
    ["openai/gpt-oss-20b", "Recommended — GPT-OSS 20B", "Fast structured lesson generation."],
    ["openai/gpt-oss-120b", "Higher quality — GPT-OSS 120B", "Larger model with a smaller free token allowance."]
  ],
  mistral: [["mistral-small-latest", "Recommended — Mistral Small", "Fast general-purpose model."]],
  openrouter: [["openrouter/free", "Automatic free model", "OpenRouter selects an available free model."]],
  custom: []
};

const listFields = [
  ["resources", "Resources"],
  ["kpis", "Main learning focus (KPIs)"],
  ["c21_skills", "C21st skills"],
  ["success_criteria", "Success criteria"],
  ["cross_curricular", "Numeracy, literacy, culture & heritage"],
  ["warm_up", "Warm up"],
  ["focused_instruction", "Focused instruction"],
  ["guided_instruction", "Guided instruction / practice"],
  ["collaborative_learning", "Collaborative learning / group task"],
  ["independent_learning", "Independent learning / individual task"],
  ["progress_check", "Progress check / independent practice"],
  ["home_learning", "Home learning"]
];

const el = id => document.getElementById(id);

function providerConfig() {
  const provider = el("provider").value;
  return {
    provider,
    model: provider === "custom" ? el("custom-model").value.trim()
      : provider === "openrouter" ? ""
      : el("model").value,
    api_key: el("api-key").value.trim(),
    base_url: provider === "custom" ? el("base-url").value.trim() : ""
  };
}

function settings() {
  const theory = Number(el("theory").value);
  return {
    module_weeks: Number(el("module-weeks").value),
    minutes_per_lesson: Number(el("lesson-minutes").value),
    lessons_per_week: Number(el("lessons-week").value),
    theory_percent: theory,
    practical_percent: 100 - theory,
    starting_date: el("start-date").value,
    student_level: el("student-level").value.trim()
  };
}

function options() {
  const extractImages = el("extract-images").checked;
  return {
    allow_inferred_titles: el("allow-inferred").checked,
    extract_images: extractImages,
    max_images_per_lesson: extractImages ? 3 : 0,
    image_placement: "automatic",
    include_practical_tasks: el("practical-tasks").checked,
    include_home_learning: el("include-homework").checked,
    include_reflection: el("include-reflection").checked
  };
}

function sourceSettings() {
  const ranged = el("read-mode").value === "range";
  return {
    source_type: el("source-type").value,
    read_mode: ranged ? "range" : "entire",
    page_from: ranged ? Number(el("page-from").value) : null,
    page_to: ranged ? Number(el("page-to").value) : null
  };
}

function setStep(number) {
  document.querySelectorAll(".step-panel").forEach(panel => panel.classList.toggle("active", Number(panel.dataset.step) === number));
  document.querySelectorAll(".step").forEach((step, index) => {
    step.classList.toggle("active", index + 1 === number);
    step.classList.toggle("complete", index + 1 < number);
  });
  window.scrollTo({ top: 250, behavior: "smooth" });
}

function unlockStep(number) {
  const step = document.querySelector(`.step[data-step-target="${number}"]`);
  if (step) step.disabled = false;
}

function busy(title, detail = "The model may need a minute.") {
  el("busy-title").textContent = title;
  el("busy-detail").textContent = detail;
  el("busy").classList.remove("hidden");
}

function idle() {
  stopProgress();
  el("busy").classList.add("hidden");
}

let progressTimer = null;
let progressDrift = null;
let progressShown = 0;
let progressSteps = "";
const RING_LENGTH = 2 * Math.PI * 52;

let progressStarted = 0;

function renderElapsed() {
  if (!progressStarted || !el("busy-elapsed")) return;
  const seconds = Math.round((Date.now() - progressStarted) / 1000);
  const shown = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
  const patience = seconds > 90
    ? " · free AI models are slow under load, this is still running"
    : "";
  el("busy-elapsed").textContent = `Working for ${shown}${patience}`;
}

function setProgressBar(percent, message) {
  progressShown = Math.max(progressShown, Math.min(99, Math.round(percent)));
  if (el("busy-ring-value")) el("busy-ring-value").style.strokeDashoffset = `${RING_LENGTH * (1 - progressShown / 100)}`;
  if (el("busy-percent")) el("busy-percent").textContent = `${progressShown}%`;
  if (message && el("busy-detail")) el("busy-detail").textContent = message;
}

const stepMarks = { pending: "○", active: "◍", done: "✓", skipped: "—", failed: "✕" };

function renderProgressSteps(steps) {
  if (!el("busy-steps")) return;
  const signature = steps.map(step => `${step.key}:${step.status}:${step.detail}`).join("|");
  if (signature === progressSteps) return;
  progressSteps = signature;
  el("busy-steps").innerHTML = steps.map(step =>
    `<li class="step-${escapeAttribute(step.status)}"><span class="tick">${stepMarks[step.status] || "○"}</span><div><strong>${escapeHtml(step.headline)}</strong>${step.detail ? `<small>${escapeHtml(step.detail)}</small>` : ""}</div></li>`
  ).join("");
}

function startProgress(runId, title) {
  stopProgress();
  progressShown = 0;
  progressSteps = "";
  if (el("busy-title")) el("busy-title").textContent = title;
  if (el("busy-steps")) el("busy-steps").innerHTML = "";
  if (el("busy-ring")) el("busy-ring").classList.remove("hidden");
  if (el("busy-spinner")) el("busy-spinner").classList.add("hidden");
  setProgressBar(1, "Starting…");

  // Creep forward slowly between server milestones so a long AI call never looks frozen.
  progressStarted = Date.now();
  progressDrift = window.setInterval(() => {
    const ceiling = progressShown < 40 ? 38 : progressShown < 85 ? 84 : 97;
    if (progressShown < ceiling) setProgressBar(progressShown + 1, "");
    renderElapsed();
  }, 1000);
  renderElapsed();

  progressTimer = window.setInterval(async () => {
    try {
      const response = await fetch(`/api/progress/${encodeURIComponent(runId)}`);
      if (!response.ok) return;
      const state = await response.json();
      if (!state.known) return;
      setProgressBar(state.percent, state.message);
      renderProgressSteps(state.steps || []);
    } catch (_) { /* a dropped poll is not worth interrupting the teacher */ }
  }, 900);
}

function stopProgress() {
  if (progressTimer) window.clearInterval(progressTimer);
  if (progressDrift) window.clearInterval(progressDrift);
  progressTimer = null;
  progressDrift = null;
  progressStarted = 0;
  if (el("busy-elapsed")) el("busy-elapsed").textContent = "";
  if (el("busy-ring")) el("busy-ring").classList.add("hidden");
  if (el("busy-spinner")) el("busy-spinner").classList.remove("hidden");
  if (el("busy-steps")) el("busy-steps").innerHTML = "";
}

function newRunId() {
  return `run-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function toast(message) {
  el("toast").textContent = message;
  el("toast").classList.remove("hidden");
  window.setTimeout(() => el("toast").classList.add("hidden"), 6500);
}

async function responseData(response) {
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed.");
    return data;
  }
  if (!response.ok) throw new Error(`Request failed with HTTP ${response.status}.`);
  return response;
}

function validateFirstStep() {
  if (!providerConfig().api_key) throw new Error("Paste your API key first.");
  if (el("provider").value !== "openrouter" && !providerConfig().model) throw new Error("Enter a model name.");
  if (!el("module-file").files[0]) throw new Error("Choose a source PDF or Word document.");
  const source = sourceSettings();
  if (source.read_mode === "range") {
    if (!Number.isInteger(source.page_from) || source.page_from < 1 || !Number.isInteger(source.page_to) || source.page_to < 1) {
      throw new Error("Enter both From and To physical PDF page numbers.");
    }
    if (source.page_to < source.page_from) throw new Error("The To page must be the same as or later than the From page.");
  }
  const values = settings();
  if (!values.starting_date) throw new Error("Choose the starting date.");
  if (!values.student_level) throw new Error("Enter the student level or class.");
}

async function analyzeModule() {
  try {
    validateFirstStep();
    const runId = newRunId();
    const form = new FormData();
    form.append("source", el("module-file").files[0]);
    form.append("provider", JSON.stringify(providerConfig()));
    form.append("options", JSON.stringify(options()));
    form.append("source_settings", JSON.stringify(sourceSettings()));
    form.append("run_id", runId);
    const selectedSource = sourceSettings();
    const rangeDetail = selectedSource.read_mode === "range"
      ? `physical PDF pages ${selectedSource.page_from}–${selectedSource.page_to}`
      : "the entire source document";
    busy("Reading the selected material…", `Finding headings, topics, objectives, and practical tasks in ${rangeDetail}.`);
    startProgress(runId, "Reading the selected material…");
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    const data = await responseData(response);
    state.jobId = data.job_id;
    state.analysis = data.analysis;
    state.images = data.images || [];
    initializeTopicPool();
    renderAnalysis();
    unlockStep(2);
    setStep(2);
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

function initializeTopicPool() {
  const selectedKeys = new Set((state.analysis.topics || []).map(topicKey));
  state.availableTopics = (state.analysis.topic_candidates || []).filter(topic => !selectedKeys.has(topicKey(topic)));
  state.emphasizedTopics = state.analysis.emphasized_topics || [];
  state.topicLibraryOpen = false;
}

function topicKey(topic) {
  return `${String(topic.title || "").trim().toLowerCase()}|${Number(topic.start_page || 1)}|${Number(topic.end_page || 1)}`;
}

function sourceRangeLabel(topic) {
  const location = state.analysis?.source_format === "docx" ? "Document part" : "PDF page";
  const start = Number(topic.start_page || 1);
  const end = Number(topic.end_page || start);
  return start === end ? `${location} ${start}` : `${location}s ${start}–${end}`;
}

function normalizedTopicTitle(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/^\d+(?:\s+\d+)*\s+/, "").trim();
}

function selectedParentFor(topic) {
  const parent = normalizedTopicTitle(topic.parent_title);
  if (!parent) return null;
  return state.analysis.topics.find(selected => normalizedTopicTitle(selected.title) === parent) || null;
}

function renderTopicEmphasis(parent) {
  const parentKey = normalizedTopicTitle(parent.title);
  const emphasized = state.emphasizedTopics.filter(topic => normalizedTopicTitle(topic.parent_title) === parentKey);
  if (!emphasized.length) return "";
  return `<div class="emphasis-list"><span>Priority coverage</span>${emphasized.map(topic => {
    const index = state.emphasizedTopics.indexOf(topic);
    return `<button type="button" class="emphasis-chip" data-remove-emphasis="${index}" title="Remove emphasis">${escapeHtml(topic.title)} <b>×</b></button>`;
  }).join("")}</div>`;
}

function renderCandidateCard(topic, index) {
  const parent = selectedParentFor(topic);
  const isSubtopic = topic.content_role === "subtopic" || Boolean(topic.parent_title);
  const actionAttribute = isSubtopic && parent ? `data-emphasize-topic="${index}"` : `data-add-topic="${index}"`;
  const actionLabel = isSubtopic && parent ? "Emphasize" : "Add topic";
  const roleLabel = isSubtopic ? (parent ? `Subtopic of ${parent.title}` : "Instructional subtopic") : "Instructional topic";
  return `<article class="candidate-card">
    <div class="candidate-card-copy"><div><span class="candidate-role">${escapeHtml(roleLabel)}</span><span class="candidate-confidence ${escapeAttribute(topic.confidence || "high")}">${escapeHtml(topic.confidence || "high")} confidence</span><span class="source-pages">${escapeHtml(sourceRangeLabel(topic))}</span></div><h3>${escapeHtml(topic.title)}</h3><p>${escapeHtml(topic.summary || topic.selection_reason || "Source-backed section detected in the selected material.")}</p>${topic.selection_reason ? `<small>${escapeHtml(topic.selection_reason)}</small>` : ""}</div>
    <button type="button" class="add-topic-button" ${actionAttribute}>${actionLabel}</button>
  </article>`;
}

function renderSelectionCapacity() {
  const selectedScore = state.analysis.topics.reduce((score, topic) => score + (topic.content_role === "subtopic" ? 1.2 : 2), 0);
  const score = selectedScore + (state.emphasizedTopics.length * 0.65);
  let level = "balanced";
  let message = "Balanced for the fixed two-page template.";
  if (score > 6) {
    level = "over";
    message = "Over capacity: the fixed template is likely to overflow. Remove a topic or emphasis before building.";
  } else if (score > 4.8) {
    level = "full";
    message = "Nearly full: all choices will be enforced, so keep only the priorities you truly need.";
  }
  el("selection-capacity").className = `selection-capacity ${level}`;
  el("selection-capacity").innerHTML = `<strong>${state.analysis.topics.length} selected topic${state.analysis.topics.length === 1 ? "" : "s"} · ${state.emphasizedTopics.length} emphasis item${state.emphasizedTopics.length === 1 ? "" : "s"}</strong><span>${message}</span>`;
}

function renderAnalysis() {
  const analysis = state.analysis;
  const start = Number(analysis.selected_start_page || 1);
  const end = Number(analysis.selected_end_page || analysis.page_count);
  const selectedCount = Number(analysis.selected_page_count || (end - start + 1));
  const isPdf = analysis.source_format !== "docx";
  const scope = isPdf
    ? (start === 1 && end === Number(analysis.page_count)
      ? `${analysis.page_count} physical PDF pages`
      : `PDF pages ${start}–${end} of ${analysis.page_count} · ${selectedCount} selected`)
    : (analysis.page_count === 1 ? "Entire Word document" : `Entire Word document · ${analysis.page_count} internal parts`);
  const sourceKind = analysis.source_type === "book" ? "Book / other document" : "Teacher module";
  el("module-summary").innerHTML = `
    <div class="summary-fields">
      <input id="review-module-title" value="${escapeAttribute(analysis.module_title)}" aria-label="Module title">
      <input id="review-course-title" value="${escapeAttribute(analysis.course_title || analysis.source_filename)}" aria-label="Course title">
    </div>
    <strong>${escapeHtml(sourceKind)} · ${scope} · ${analysis.topics.length} topics</strong>`;
  el("warning-list").innerHTML = (analysis.warnings || []).map(item => `<div class="warning-item">${escapeHtml(item)}</div>`).join("");
  el("topic-list").innerHTML = analysis.topics.map((topic, index) => `
    <div class="topic-card" draggable="true" data-topic-index="${index}">
      <span class="topic-index"><i class="drag-handle">⋮⋮</i>${String(index + 1).padStart(2, "0")}</span>
      <div>
        <input data-topic-title value="${escapeAttribute(topic.title)}" aria-label="Topic ${index + 1} title">
        <small>${escapeHtml(topic.summary || topic.subtopics.join(" · "))}${topic.title_inferred ? ' <span class="inferred-note">· inferred — review this</span>' : ""}</small>${renderTopicEmphasis(topic)}
      </div>
      <div class="topic-card-actions"><span class="source-pages">${escapeHtml(sourceRangeLabel(topic))}</span><button type="button" class="remove-topic-button" data-remove-topic="${index}">Remove</button></div>
    </div>`).join("");
  el("candidate-count").textContent = state.availableTopics.length ? `(${state.availableTopics.length})` : "";
  renderSelectionCapacity();
  el("topic-library").classList.toggle("hidden", !state.topicLibraryOpen);
  el("candidate-list").innerHTML = state.availableTopics.length
    ? state.availableTopics.map(renderCandidateCard).join("")
    : '<div class="empty-candidate-list"><strong>No other grounded topics found.</strong><p>The selected cards already cover the teachable structure detected in this source range.</p></div>';
}

function syncTopicReviewFields() {
  state.analysis.module_title = el("review-module-title").value.trim();
  state.analysis.course_title = el("review-course-title").value.trim();
  document.querySelectorAll("#topic-list .topic-card").forEach((card, index) => {
    const topic = state.analysis.topics[index];
    topic.title = card.querySelector("[data-topic-title]").value.trim();
  });
}

function captureTopicEdits() {
  syncTopicReviewFields();
  if (!state.analysis.module_title) throw new Error("Enter the module title.");
  if (!state.analysis.topics.length || state.analysis.topics.some(topic => !topic.title)) throw new Error("Every retained section needs a title.");
  state.analysis.topic_candidates = state.availableTopics;
  state.analysis.emphasized_topics = state.emphasizedTopics;
}

async function generatePlans() {
  try {
    captureTopicEdits();
      const runId = newRunId();
    busy("Building the module plan…", "The template grows to fit the plan you asked for.");
    startProgress(runId, "Building the lesson plan…");
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, analysis: state.analysis, settings: settings(), options: options(), provider: providerConfig(), run_id: runId })
    });
    const data = await responseData(response);
    state.plan = data.plan;
    // The plan is usable even when the model left a selected topic thin: say so and let the
    // teacher fix it in the editor rather than losing the whole plan.
    if (state.plan?.coverage_warning) {
      toast(`${state.plan.coverage_warning} You can add it yourself in the editor.`);
    }
    renderLessons();
    unlockStep(3);
    setStep(3);
    await refreshPreview(false);
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

function renderListEditor(lessonIndex, key, label, items, full = false) {
  const formatting = state.plan?.lessons?.[lessonIndex]?.text_formatting?.[key] || [];
  const rows = (items || []).length
    ? items.map((item, itemIndex) => {
      const format = formatting[itemIndex] || {};
      const boldValue = typeof format.bold === "boolean" ? String(format.bold) : "";
      const italicValue = typeof format.italic === "boolean" ? String(format.italic) : "";
      const fontSize = format.font_size || "";
      const textareaStyle = `${format.bold === true ? "font-weight:700;" : format.bold === false ? "font-weight:400;" : ""}${format.italic === true ? "font-style:italic;" : format.italic === false ? "font-style:normal;" : ""}${fontSize ? `font-size:${fontSize}pt;` : ""}`;
      return `<div class="editable-item" data-item-index="${itemIndex}" data-format-bold="${boldValue}" data-format-italic="${italicValue}" data-format-size="${fontSize}">
        <textarea data-edit-text style="${textareaStyle}">${escapeHtml(item)}</textarea>
        <button class="item-action rewrite-item" type="button" data-rewrite-item title="Ask AI to rewrite">✦</button>
        <button class="item-action delete-item" type="button" data-delete-item title="Delete paragraph">×</button>
        <div class="paragraph-format-controls">
          <span>Format this point</span>
          <button type="button" data-format-action="normal" class="${format.bold === false && format.italic === false ? "active" : ""}">Normal</button>
          <button type="button" data-format-action="bold" class="format-bold ${format.bold === true ? "active" : ""}">B</button>
          <button type="button" data-format-action="italic" class="format-italic ${format.italic === true ? "active" : ""}">I</button>
          <select data-format-size aria-label="Text size"><option value="" ${!fontSize ? "selected" : ""}>Auto size</option>${[8.5, 9.25, 10, 11, 12, 14].map(size => `<option value="${size}" ${Number(fontSize) === size ? "selected" : ""}>${size} pt</option>`).join("")}</select>
        </div>
      </div>`;
    }).join("")
    : '<div class="empty-list">No paragraph added.</div>';
  return `<section class="list-edit ${full ? "full" : ""}" data-lesson="${lessonIndex}" data-field="${key}">
    <header><span>${label}</span><button class="add-line" type="button" data-add-line title="Add paragraph">+</button></header>
    <div class="item-stack">${rows}</div>
  </section>`;
}

function normalizedSelectedImages(lesson) {
  if (Array.isArray(lesson.selected_images)) return lesson.selected_images;
  const field = lesson.recommended_image_stage || "focused_instruction";
  lesson.selected_images = (lesson.selected_image_xrefs || []).map(xref => ({ xref: Number(xref), field }));
  return lesson.selected_images;
}

function syncSelectedImageXrefs(lesson) {
  lesson.selected_image_xrefs = normalizedSelectedImages(lesson).map(item => Number(item.xref));
}

function lessonImages(lesson, targetField = null) {
  const placements = normalizedSelectedImages(lesson);
  const chosenXrefs = new Set(placements.map(item => Number(item.xref)));
  const eligible = state.analysis?.source_format === "docx"
    ? state.images
    : state.images.filter(image => image.page >= lesson.start_page && image.page <= lesson.end_page);
  const chosen = placements
    .filter(item => !targetField || item.field === targetField)
    .map(item => {
      const image = eligible.find(candidate => Number(candidate.xref) === Number(item.xref));
      return image ? { ...image, field: item.field } : null;
    })
    .filter(Boolean);
  return { chosen, available: eligible.filter(image => !chosenXrefs.has(Number(image.xref))) };
}

function syncImagePickControls() {
  // Read the rendered checkboxes rather than trusting a count held in state, so the button can
  // never disagree with what the teacher is looking at.
  const boxes = [...document.querySelectorAll("[data-pick-image]")];
  const checked = boxes.filter(box => box.checked).length;
  const toggle = document.querySelector("[data-select-all-images]");
  if (toggle) toggle.textContent = boxes.length && checked >= boxes.length ? "Unselect all" : "Select all";
  const download = document.querySelector("[data-download-images]");
  if (download) {
    download.disabled = checked === 0;
    download.textContent = `⤓ Download ${checked || "selected"}`;
  }
}

function renderImageManager(lesson, lessonIndex, targetField = null) {
  if (!state.plan.options?.extract_images) return "";
  const { chosen, available } = lessonImages(lesson, targetField);
  const selected = chosen.length ? chosen.map(image => `
    <div class="selected-image">
      <img src="${escapeAttribute(image.url)}" alt="Extracted source figure from ${escapeAttribute(sourceImageLocation(image))}">
      <small>${escapeHtml(sourceImageLocation(image))} · ${escapeHtml(fieldLabels[image.field] || image.field)}</small>
      <button class="remove-image" type="button" data-remove-image="${image.xref}" data-image-field="${escapeAttribute(image.field)}" title="Remove image">×</button>
    </div>`).join("") : `<div class="empty-list">${targetField ? "No image in this section." : "No plan image selected."}</div>`;
  // Downloading offers every extracted image, including branding the AI is not allowed to use.
  const downloadable = state.images || [];
  const ticked = state.imageDownloadPicks ||= new Set();
  const gallery = targetField && (available.length || downloadable.length) ? `<div class="image-gallery-wrap">
    <div class="gallery-heading">
      <strong>Available source images</strong>
      <div class="gallery-download">
        <button type="button" class="text-button-static" data-select-all-images>${downloadable.length && ticked.size >= downloadable.length ? "Unselect all" : "Select all"}</button>
        <button type="button" class="download-images" data-download-images ${ticked.size ? "" : "disabled"}>⤓ Download ${ticked.size || "selected"}</button>
      </div>
    </div>
    <div class="image-gallery">${downloadable.map(image => {
      const name = String(image.url || "").split("/").pop();
      const inLesson = available.some(item => Number(item.xref) === Number(image.xref));
      return `
      <div class="gallery-item${image.lesson_candidate === false ? " not-lesson" : ""}">
        <label class="gallery-pick" title="Include in download">
          <input type="checkbox" data-pick-image="${escapeAttribute(name)}" ${ticked.has(name) ? "checked" : ""}>
        </label>
        <button class="gallery-image" type="button" ${inLesson ? `data-add-image="${image.xref}" data-image-field="${escapeAttribute(targetField)}"` : "disabled"}>
          <img src="${escapeAttribute(image.url)}" alt="Source figure from ${escapeAttribute(sourceImageLocation(image))}">
          <small>${escapeHtml(sourceImageLocation(image))}${image.lesson_candidate === false ? ` · ${escapeHtml(image.skip_reason || "not lesson material")}` : ""}</small>
        </button>
      </div>`;
    }).join("")}</div>
  </div>` : "";
  const askAi = targetField && available.length
    ? `<button class="ai-image-button" type="button" data-suggest-image data-image-field="${escapeAttribute(targetField)}">✦ Ask AI to choose an image for this section</button>` : "";
  return `<section class="image-manager" data-lesson-images="${lessonIndex}">
    <header><h4>${targetField ? `Images in ${escapeHtml(fieldLabels[targetField])}` : "Plan images"}</h4><span>Up to three images, one per section</span></header>
    <div class="image-row">${selected}</div>${askAi}${gallery}
  </section>`;
}

function sourceImageLocation(image) {
  return state.analysis?.source_format === "docx" ? "Word document" : `PDF page ${image.page}`;
}

function renderLessons(openIndex = 0) {
  el("lesson-list").innerHTML = state.plan.lessons.map((lesson, index) => {
    const fields = listFields.map(([key, label]) => renderListEditor(
      index,
      key,
      label,
      lesson[key] || [],
      ["focused_instruction", "guided_instruction", "collaborative_learning", "independent_learning"].includes(key)
    )).join("");
    return `<details class="lesson-card" data-lesson-card="${index}" ${index === openIndex ? "open" : ""}>
      <summary>
        <span class="lesson-number">${String(index + 1).padStart(2, "0")}</span>
        <div><h3>${escapeHtml(lesson.title)}</h3><small>${escapeHtml(lesson.page_range)} · ${escapeHtml(lesson.source_topics.join(", "))}</small></div>
        <span class="lesson-type">${escapeHtml(lesson.lesson_type)}</span>
      </summary>
      <div class="lesson-editor">
        <label class="full">Lesson title<input data-lesson-title value="${escapeAttribute(lesson.title)}"></label>
        ${fields}
        ${renderImageManager(lesson, index)}
        <section class="list-edit full" data-lesson="${index}" data-field="reflection" data-single-field>
          <header><span>Reflection</span></header>
          <div class="item-stack"><div class="editable-item single"><textarea data-edit-text>${escapeHtml(lesson.reflection || "")}</textarea><button class="item-action rewrite-item" type="button" data-rewrite-item title="Ask AI to rewrite">✦</button><button class="item-action delete-item" type="button" data-delete-item title="Clear reflection">×</button></div></div>
        </section>
      </div>
    </details>`;
  }).join("");
}

function captureLessonEdits() {
  document.querySelectorAll("[data-lesson-card]").forEach(card => {
    const lessonIndex = Number(card.dataset.lessonCard);
    const lesson = state.plan.lessons[lessonIndex];
    lesson.title = card.querySelector("[data-lesson-title]").value.trim();
    card.querySelectorAll(".list-edit[data-field]").forEach(section => {
      const entries = [...section.querySelectorAll(".editable-item")].map(item => ({
        value: item.querySelector("[data-edit-text]")?.value.trim() || "",
        format: formattingFromItem(item)
      })).filter(entry => entry.value);
      const values = entries.map(entry => entry.value);
      lesson[section.dataset.field] = section.hasAttribute("data-single-field") ? (values[0] || "") : values;
      lesson.text_formatting ||= {};
      lesson.text_formatting[section.dataset.field] = entries.map(entry => entry.format);
    });
  });
}

function formattingFromItem(item) {
  const bold = item.dataset.formatBold;
  const italic = item.dataset.formatItalic;
  const size = Number(item.dataset.formatSize || 0);
  const format = {};
  if (bold === "true" || bold === "false") format.bold = bold === "true";
  if (italic === "true" || italic === "false") format.italic = italic === "true";
  if (size) format.font_size = size;
  return format;
}

function applyParagraphFormatControl(control) {
  const item = control.closest(".editable-item");
  if (!item) return;
  const action = control.dataset.formatAction;
  if (action === "normal") {
    item.dataset.formatBold = "false";
    item.dataset.formatItalic = "false";
  } else if (action === "bold") {
    item.dataset.formatBold = item.dataset.formatBold === "true" ? "false" : "true";
  } else if (action === "italic") {
    item.dataset.formatItalic = item.dataset.formatItalic === "true" ? "false" : "true";
  }
  const textarea = item.querySelector("[data-edit-text]");
  textarea.style.fontWeight = item.dataset.formatBold === "true" ? "700" : item.dataset.formatBold === "false" ? "400" : "";
  textarea.style.fontStyle = item.dataset.formatItalic === "true" ? "italic" : item.dataset.formatItalic === "false" ? "normal" : "";
  item.querySelector('[data-format-action="bold"]')?.classList.toggle("active", item.dataset.formatBold === "true");
  item.querySelector('[data-format-action="italic"]')?.classList.toggle("active", item.dataset.formatItalic === "true");
  item.querySelector('[data-format-action="normal"]')?.classList.toggle("active", item.dataset.formatBold === "false" && item.dataset.formatItalic === "false");
  if (item.closest("#section-inspector")) syncInspector();
  else captureLessonEdits();
  markPreviewDirty();
}

function applyParagraphFontSize(select) {
  const item = select.closest(".editable-item");
  if (!item) return;
  item.dataset.formatSize = select.value;
  item.querySelector("[data-edit-text]").style.fontSize = select.value ? `${select.value}pt` : "";
  if (item.closest("#section-inspector")) syncInspector();
  else captureLessonEdits();
  markPreviewDirty();
}

function syncAllEdits() {
  if (state.inspectorDirty) syncInspector();
  else captureLessonEdits();
}

function buildArtifactForm() {
  syncAllEdits();
  const form = new FormData();
  form.append("job_id", state.jobId);
  form.append("analysis", JSON.stringify(state.analysis));
  form.append("plan", JSON.stringify(state.plan));
  const template = el("template-file").files[0];
  if (template) form.append("template", template);
  return form;
}

async function refreshPreview(showBusy = true) {
  const viewport = capturePreviewViewport();
  try {
    if (showBusy) busy("Refreshing the actual PDF…", "Applying your edits to the supplied template.");
    const response = await fetch("/api/preview", { method: "POST", body: buildArtifactForm() });
    const data = await responseData(response);
    renderPdfPages(data.pages || []);
    await waitForPreviewImages();
    renderLessons(state.previewSelection?.lessonIndex || 0);
    state.inspectorDirty = false;
    // A longer plan is allowed: the template grows with it. The page count is information.
    const pageCount = Number(data.page_count) || (data.pages || []).length;
    el("preview-status").textContent = pageCount === 2
      ? "Word preview is up to date"
      : `Word preview is up to date · ${pageCount} pages`;
    if (data.layout_message) toast(data.layout_message);
    document.querySelector(".preview-dot").classList.add("ready");
    restorePreviewViewport(viewport);
    return true;
  } catch (error) {
    el("preview-status").textContent = "Word preview could not be generated";
    toast(error.message);
    return false;
  } finally { if (showBusy) idle(); }
}

function capturePreviewViewport() {
  const pages = [...document.querySelectorAll(".pdf-page")];
  if (!pages.length) return null;
  const reference = window.scrollY + window.innerHeight / 2;
  let selected = pages[0];
  let distance = Infinity;
  for (const page of pages) {
    const top = page.getBoundingClientRect().top + window.scrollY;
    const bottom = top + page.getBoundingClientRect().height;
    const currentDistance = reference < top ? top - reference : reference > bottom ? reference - bottom : 0;
    if (currentDistance < distance) {
      distance = currentDistance;
      selected = page;
    }
  }
  const top = selected.getBoundingClientRect().top + window.scrollY;
  const height = Math.max(selected.getBoundingClientRect().height, 1);
  return {
    pageNumber: selected.dataset.pageNumber,
    ratio: Math.min(1, Math.max(0, (reference - top) / height)),
  };
}

async function waitForPreviewImages() {
  const images = [...document.querySelectorAll(".pdf-page > img")];
  await Promise.all(images.map(image => {
    if (image.complete) return Promise.resolve();
    return new Promise(resolve => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener("error", resolve, { once: true });
    });
  }));
}

function restorePreviewViewport(viewport) {
  if (!viewport) return;
  const page = document.querySelector(`.pdf-page[data-page-number="${viewport.pageNumber}"]`);
  if (!page) return;
  requestAnimationFrame(() => {
    const top = page.getBoundingClientRect().top + window.scrollY;
    const target = top + (page.getBoundingClientRect().height * viewport.ratio) - window.innerHeight / 2;
    window.scrollTo({ top: Math.max(0, target), behavior: "auto" });
  });
}

function renderPdfPages(pages) {
  state.previewHotspots = pages.flatMap(page => page.hotspots || []);
  if (!pages.length) {
    el("pdf-pages").innerHTML = '<div class="preview-placeholder">No PDF pages were generated.</div>';
    return;
  }
  el("pdf-pages").innerHTML = pages.map(page => {
    const overlays = (page.hotspots || []).map(hotspot => {
      const container = hotspot.container || {};
      const sourceImage = hotspot.kind === "image"
        ? state.images.find(image => Number(image.xref) === Number(hotspot.source_xref)) : null;
      const imageMarkup = sourceImage
        ? `<img class="live-drag-image" src="${escapeAttribute(sourceImage.url)}" alt="Movable lesson image">` : "";
      return `<button class="pdf-hotspot ${hotspot.kind === "image" ? "image-hotspot" : ""}" type="button"
        data-preview-field="${hotspot.field}" data-lesson-index="${page.lesson_index}" data-label="${escapeAttribute(hotspot.label)}"
        data-hotspot-kind="${hotspot.kind || "text"}" data-source-xref="${hotspot.source_xref || ""}"
        data-container-left="${container.left ?? ""}" data-container-top="${container.top ?? ""}"
        data-container-width="${container.width ?? ""}" data-container-height="${container.height ?? ""}"
        style="left:${hotspot.left}%;top:${hotspot.top}%;width:${hotspot.width}%;height:${hotspot.height}%" aria-label="Edit ${escapeAttribute(hotspot.label)}">${imageMarkup}</button>`;
    }).join("");
    return `<div class="pdf-page" data-page-number="${page.page_number}" data-page-width-points="${page.width_points}" data-page-height-points="${page.height_points}"><img src="${escapeAttribute(page.url)}?v=${Date.now()}" alt="Rendered Word preview page ${page.page_number}">${overlays}<span class="pdf-page-number">Page ${page.page_number}</span></div>`;
  }).join("");
  applyPreviewZoom();
}

function applyPreviewZoom() {
  state.previewLayout = el("preview-zoom").value || "large";
  document.querySelector(".pdf-review-layout")?.classList.toggle("preview-wide", state.previewLayout === "large");
}

function selectPreviewField(lessonIndex, field) {
  if (state.inspectorDirty) syncInspector();
  state.previewSelection = { lessonIndex, field };
  if (state.sectionProposal && (state.sectionProposal.lessonIndex !== lessonIndex || state.sectionProposal.field !== field)) {
    state.sectionProposal = null;
  }
  document.querySelectorAll(".pdf-hotspot").forEach(item => item.classList.toggle(
    "active",
    Number(item.dataset.lessonIndex) === lessonIndex && item.dataset.previewField === field
  ));
  renderInspector();
}

function renderInspector() {
  const selection = state.previewSelection;
  if (!selection) return;
  const lesson = state.plan.lessons[selection.lessonIndex];
  const field = selection.field;
  let editor;
  let imageManager = "";
  if (field === "images") editor = renderImageManager(lesson, selection.lessonIndex) || '<div class="empty-list">Image extraction is turned off.</div>';
  else if (field === "title") editor = renderListEditor(selection.lessonIndex, field, fieldLabels[field], [lesson.title], false).replace('class="list-edit ', 'class="list-edit inspector-single ').replace('data-field="title"', 'data-field="title" data-single-field');
  else if (field === "reflection") editor = renderListEditor(selection.lessonIndex, field, fieldLabels[field], [lesson.reflection || ""], false).replace('data-field="reflection"', 'data-field="reflection" data-single-field');
  else {
    editor = renderListEditor(selection.lessonIndex, field, fieldLabels[field], lesson[field] || [], false);
    if (imageCapableFields.has(field)) imageManager = renderImageManager(lesson, selection.lessonIndex, field);
  }
  const sectionAssistant = Array.isArray(lesson[field]) && fieldItemLimits[field]
    ? renderSectionAssistant(selection.lessonIndex, field, lesson[field]) : "";
  el("section-inspector").innerHTML = `
    <div class="inspector-heading"><p class="eyebrow">Selected section</p><h3>${escapeHtml(fieldLabels[field] || field)}</h3><span id="capacity-status" class="capacity-status"></span></div>
    <div class="inspector-body">${editor}${sectionAssistant}${imageManager}</div>
    <div class="inspector-actions"><button class="primary-button" type="button" data-inspector-refresh>Apply and refresh Word preview</button></div>`;
  syncImagePickControls();
  updateCapacityStatus();
}

function renderSectionAssistant(lessonIndex, field, items) {
  const limit = fieldItemLimits[field];
  const over = items.length >= limit;
  const proposal = state.sectionProposal?.lessonIndex === lessonIndex && state.sectionProposal?.field === field
    ? state.sectionProposal : null;
  const proposalMarkup = proposal ? `<div class="section-ai-proposal">
    <strong>Proposed section</strong>
    <ol>${proposal.items.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
    ${proposal.note ? `<p>${escapeHtml(proposal.note)}</p>` : ""}
    <div><button type="button" class="secondary-button" data-discard-section-proposal>Discard</button><button type="button" class="primary-button" data-apply-section-proposal>Apply section</button></div>
  </div>` : "";
  return `<section class="section-ai-assistant">
    <header><div><strong>✦ Ask AI about this whole section</strong><small>${items.length} point${items.length === 1 ? "" : "s"} · ${over ? `past the ~${limit} the template fits comfortably — check the two-page preview` : `template fits about ${limit}`} · grounded in the uploaded source</small></div><button type="button" class="add-blank-point" data-add-section-point>＋ Add blank point</button></header>
    <div class="section-ai-quick">
      <button type="button" data-section-ai-prompt="Add one more source-grounded point in addition to the existing points, keeping the existing points unchanged.">Add 1 more point</button>
      <button type="button" data-section-ai-prompt="Add two more source-grounded points in addition to the existing points, keeping the existing points unchanged.">Add 2 more points</button>
      <button type="button" data-section-ai-prompt="Improve the whole section for clarity, balance, and alignment while preserving its meaning.">Improve section</button>
      <button type="button" data-section-ai-prompt="Make the whole section more concise without losing important source-grounded content.">Make concise</button>
    </div>
    <textarea id="section-ai-instruction" placeholder="Tell the AI what to add, include, remove, emphasize, or change across this entire section."></textarea>
    <button type="button" class="run-section-ai" data-run-section-ai>Send instruction to AI</button>
    ${proposalMarkup}
  </section>`;
}

async function reviseWholeSection(instruction) {
  const selection = state.previewSelection;
  if (!selection) return;
  syncInspector();
  const lesson = state.plan.lessons[selection.lessonIndex];
  try {
    busy("Reworking the whole section…", "Using the lesson and the already-read source material.");
    const response = await fetch("/api/revise-section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerConfig(), job_id: state.jobId, analysis: state.analysis,
        lesson, field: selection.field, items: lesson[selection.field] || [], instruction
      })
    });
    const data = await responseData(response);
    state.sectionProposal = {
      lessonIndex: selection.lessonIndex, field: selection.field,
      items: data.items || [], note: data.note || "", limit: data.limit
    };
    renderInspector();
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

function syncInspector() {
  const selection = state.previewSelection;
  if (!selection) return;
  const lesson = state.plan.lessons[selection.lessonIndex];
  const section = el("section-inspector").querySelector(".list-edit[data-field]");
  if (!section) return;
  const entries = [...section.querySelectorAll(".editable-item")].map(item => ({
    value: item.querySelector("[data-edit-text]")?.value.trim() || "",
    format: formattingFromItem(item)
  })).filter(entry => entry.value);
  const values = entries.map(entry => entry.value);
  lesson[selection.field] = section.hasAttribute("data-single-field") ? (values[0] || "") : values;
  lesson.text_formatting ||= {};
  lesson.text_formatting[selection.field] = entries.map(entry => entry.format);
  state.inspectorDirty = true;
  markPreviewDirty();
  updateCapacityStatus();
}

function updateCapacityStatus() {
  const selection = state.previewSelection;
  const status = el("capacity-status");
  if (!selection || !status || selection.field === "images") return;
  const lesson = state.plan.lessons[selection.lessonIndex];
  const value = lesson[selection.field];
  const text = Array.isArray(value) ? value.join(" ") : String(value || "");
  const measured = state.previewHotspots
    .filter(hotspot => hotspot.field === selection.field && hotspot.capacity)
    .map(hotspot => Number(hotspot.capacity));
  const limit = measured.length ? Math.max(...measured) : (fieldCapacity[selection.field] || 350);
  const ratio = text.length / limit;
  if (ratio <= 1) status.textContent = `${text.length}/${limit} characters — normal template size`;
  else if (ratio <= 1.5) status.textContent = `${text.length}/${limit} characters — auto-fit will reduce this section slightly`;
  else status.textContent = `${text.length}/${limit} characters — minimum readable size; preview may add space`;
  status.classList.toggle("over", ratio > 1.5);
}

function markPreviewDirty() {
  el("preview-status").textContent = "Edits not yet applied to the Word preview";
  document.querySelector(".preview-dot").classList.remove("ready");
}

function createLiveWrapSimulator(page, hotspot, pageRect) {
  const containerLeft = parseFloat(hotspot.dataset.containerLeft);
  const containerTop = parseFloat(hotspot.dataset.containerTop);
  const containerWidth = parseFloat(hotspot.dataset.containerWidth);
  const containerHeight = parseFloat(hotspot.dataset.containerHeight);
  const width = pageRect.width * containerWidth / 100;
  const height = pageRect.height * containerHeight / 100;
  const canvas = document.createElement("canvas");
  canvas.className = "live-wrap-simulator";
  canvas.style.left = `${containerLeft}%`;
  canvas.style.top = `${containerTop}%`;
  canvas.style.width = `${containerWidth}%`;
  canvas.style.height = `${containerHeight}%`;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  page.appendChild(canvas);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const lesson = state.plan.lessons[Number(hotspot.dataset.lessonIndex)];
  const value = lesson[hotspot.dataset.previewField];
  const items = Array.isArray(value) ? value : [String(value || "")];
  const image = hotspot.querySelector(".live-drag-image");
  if (image && !image.complete) image.addEventListener("load", () => state.imageDrag && drawLiveWrapPreview(state.imageDrag), { once: true });
  return { canvas, context, width, height, containerLeft, containerTop, containerWidth, containerHeight, items, image, field: hotspot.dataset.previewField };
}

function drawLiveWrapPreview(drag) {
  const simulation = drag?.simulator;
  if (!simulation) return;
  const { context, width, height } = simulation;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fff";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "#172432";
  context.lineWidth = 1;
  context.strokeRect(.5, .5, width - 1, height - 1);

  const headerHeight = Math.min(20, Math.max(14, height * .16));
  context.fillStyle = "#e6eef1";
  context.fillRect(1, 1, width - 2, headerHeight);
  context.fillStyle = "#172432";
  context.font = `700 ${Math.max(8, Math.min(11, width / 58))}px Arial, sans-serif`;
  context.textBaseline = "middle";
  context.fillText(fieldLabels[simulation.field] || simulation.field, 6, headerHeight / 2 + 1);

  const imageLeftPage = parseFloat(drag.hotspot.style.left);
  const imageTopPage = parseFloat(drag.hotspot.style.top);
  const imageWidthPage = parseFloat(drag.hotspot.style.width);
  const imageHeightPage = parseFloat(drag.hotspot.style.height);
  const imageX = ((imageLeftPage - simulation.containerLeft) / simulation.containerWidth) * width;
  const imageY = ((imageTopPage - simulation.containerTop) / simulation.containerHeight) * height;
  const imageWidth = (imageWidthPage / simulation.containerWidth) * width;
  const imageHeight = (imageHeightPage / simulation.containerHeight) * height;

  if (simulation.image?.complete) {
    context.drawImage(simulation.image, imageX, imageY, imageWidth, imageHeight);
  }
  context.strokeStyle = "#087b70";
  context.lineWidth = 2;
  context.strokeRect(imageX, imageY, imageWidth, imageHeight);

  const margin = 7;
  const fontSize = Math.max(8, Math.min(11, width / 64));
  const lineHeight = fontSize * 1.24;
  context.font = `600 ${fontSize}px Arial, sans-serif`;
  context.textBaseline = "alphabetic";
  context.fillStyle = "#172432";
  let y = headerHeight + lineHeight;
  let overflow = false;

  const laneAt = currentY => {
    const overlapsImage = currentY > imageY - lineHeight && currentY - lineHeight < imageY + imageHeight;
    if (!overlapsImage) return { x: margin, width: width - margin * 2 };
    const imageOnRight = imageX + imageWidth / 2 >= width / 2;
    if (imageOnRight) return { x: margin, width: Math.max(35, imageX - margin * 2) };
    const x = imageX + imageWidth + margin;
    return { x, width: Math.max(35, width - x - margin) };
  };

  const drawItem = text => {
    const words = String(text).replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
    let line = "";
    let firstLine = true;
    while (words.length || line) {
      let lane = laneAt(y);
      if (lane.width < 45) {
        y = imageY + imageHeight + lineHeight;
        lane = laneAt(y);
      }
      while (words.length) {
        let word = words[0];
        const prefix = firstLine && !line ? "• " : "";
        const candidate = line ? `${line} ${word}` : `${prefix}${word}`;
        if (context.measureText(candidate).width <= lane.width) {
          line = candidate;
          words.shift();
          firstLine = false;
          continue;
        }
        if (!line && word.length > 1) {
          let piece = "";
          while (word && context.measureText(`${prefix}${piece}${word[0]}`).width <= lane.width) {
            piece += word[0];
            word = word.slice(1);
          }
          line = `${prefix}${piece || word[0]}`;
          words[0] = piece ? word : word.slice(1);
          firstLine = false;
        }
        break;
      }
      if (y > height - margin) { overflow = true; return; }
      context.fillText(line, lane.x, y);
      line = "";
      y += lineHeight;
    }
    y += lineHeight * .28;
  };

  for (const item of simulation.items) {
    drawItem(item);
    if (overflow) break;
  }
  if (overflow) {
    context.fillStyle = "rgba(176,68,55,.92)";
    context.fillRect(0, height - 16, width, 16);
    context.fillStyle = "white";
    context.font = "700 9px Arial, sans-serif";
    context.fillText("Content needs more space", 6, height - 5);
  }
}

function startImageDrag(event) {
  const hotspot = event.target.closest('.image-hotspot[data-hotspot-kind="image"]');
  if (!hotspot || hotspot.dataset.containerWidth === "") return;
  event.preventDefault();
  const page = hotspot.closest(".pdf-page");
  const pageRect = page.getBoundingClientRect();
  const simulator = createLiveWrapSimulator(page, hotspot, pageRect);
  state.imageDrag = {
    hotspot,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startLeft: parseFloat(hotspot.style.left),
    startTop: parseFloat(hotspot.style.top),
    pageWidth: pageRect.width,
    pageHeight: pageRect.height,
    pageWidthPoints: Number(page.dataset.pageWidthPoints || 841.89),
    pageHeightPoints: Number(page.dataset.pageHeightPoints || 595.3),
    simulator,
    moved: false,
  };
  hotspot.setPointerCapture(event.pointerId);
  hotspot.classList.add("dragging");
  hotspot.style.opacity = "0";
  drawLiveWrapPreview(state.imageDrag);
}

function moveImageDrag(event) {
  const drag = state.imageDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  event.preventDefault();
  const dx = ((event.clientX - drag.startX) / drag.pageWidth) * 100;
  const dy = ((event.clientY - drag.startY) / drag.pageHeight) * 100;
  const hotspotWidth = parseFloat(drag.hotspot.style.width);
  const hotspotHeight = parseFloat(drag.hotspot.style.height);
  const containerLeft = parseFloat(drag.hotspot.dataset.containerLeft);
  const containerTop = parseFloat(drag.hotspot.dataset.containerTop);
  const containerWidth = parseFloat(drag.hotspot.dataset.containerWidth);
  const containerHeight = parseFloat(drag.hotspot.dataset.containerHeight);
  const left = Math.min(containerLeft + containerWidth - hotspotWidth, Math.max(containerLeft, drag.startLeft + dx));
  const top = Math.min(containerTop + containerHeight - hotspotHeight, Math.max(containerTop, drag.startTop + dy));
  drag.hotspot.style.left = `${left}%`;
  drag.hotspot.style.top = `${top}%`;
  drawLiveWrapPreview(drag);
  if (Math.abs(dx) > 0.2 || Math.abs(dy) > 0.2) drag.moved = true;
}

function finishImageDrag(event) {
  const drag = state.imageDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const hotspot = drag.hotspot;
  hotspot.classList.remove("dragging");
  try { hotspot.releasePointerCapture(event.pointerId); } catch (_) {}
  if (drag.moved) {
    const containerLeft = parseFloat(hotspot.dataset.containerLeft);
    const containerTop = parseFloat(hotspot.dataset.containerTop);
    const containerWidth = parseFloat(hotspot.dataset.containerWidth);
    const containerHeight = parseFloat(hotspot.dataset.containerHeight);
    const width = parseFloat(hotspot.style.width);
    const height = parseFloat(hotspot.style.height);
    const leftRange = Math.max(containerWidth - width, 0.001);
    const topRange = Math.max(containerHeight - height, 0.001);
    const xRatio = Math.min(1, Math.max(0, (parseFloat(hotspot.style.left) - containerLeft) / leftRange));
    const yRatio = Math.min(1, Math.max(0, (parseFloat(hotspot.style.top) - containerTop) / topRange));
    const lesson = state.plan.lessons[Number(hotspot.dataset.lessonIndex)];
    const selected = normalizedSelectedImages(lesson);
    const sourceXref = Number(hotspot.dataset.sourceXref);
    const item = selected.find(image => Number(image.xref) === sourceXref)
      || selected.find(image => image.field === hotspot.dataset.previewField);
    if (item) {
      const deltaXPoints = ((parseFloat(hotspot.style.left) - drag.startLeft) / 100) * drag.pageWidthPoints;
      const deltaYPoints = ((parseFloat(hotspot.style.top) - drag.startTop) / 100) * drag.pageHeightPoints;
      item.x_ratio = Number(xRatio.toFixed(4));
      item.y_ratio = Number(yRatio.toFixed(4));
      item.offset_x_points = Number(((Number(item.offset_x_points) || 0) + deltaXPoints).toFixed(3));
      item.offset_y_points = Number(((Number(item.offset_y_points) || 0) + deltaYPoints).toFixed(3));
      syncSelectedImageXrefs(lesson);
      state.inspectorDirty = true;
      markPreviewDirty();
      hotspot.dataset.dragged = "true";
      selectPreviewField(Number(hotspot.dataset.lessonIndex), hotspot.dataset.previewField);
      state.imageDrag = null;
      const simulator = drag.simulator?.canvas;
      refreshPreview().finally(() => { if (simulator?.isConnected) simulator.remove(); });
      return;
    }
  }
  hotspot.style.opacity = "1";
  if (drag.simulator?.canvas?.isConnected) drag.simulator.canvas.remove();
  state.imageDrag = null;
}

async function exportPlans() {
  try {
    const template = el("template-file").files[0];
    if (!template && !state.config.default_template_available) throw new Error("Choose the Word template first.");
    syncAllEdits();
    if (!document.querySelector(".preview-dot").classList.contains("ready")) {
      const refreshed = await refreshPreview();
      if (!refreshed) return;
    }
    const form = buildArtifactForm();
    busy("Writing the editable Word document…", "Preserving the fixed stages and template layout.");
    const response = await fetch("/api/export", { method: "POST", body: form });
    await responseData(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i)?.[1];
    link.download = filename ? decodeURIComponent(filename) : "generated-lesson-plan.docx";
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

function openRewrite(button) {
  const item = button.closest(".editable-item");
  const section = button.closest(".list-edit");
  const input = item.querySelector("[data-edit-text]");
  state.rewriteTarget = { input, lessonIndex: Number(section.dataset.lesson), field: section.dataset.field };
  el("rewrite-original").textContent = input.value;
  el("rewrite-instruction").value = "";
  state.rewriteSuggestions = [];
  state.rewriteSuggestionInstruction = "";
  el("rewrite-dialog").showModal();
  loadRewriteSuggestions("", false);
}

function renderRewriteSuggestions(loading = false, error = "") {
  if (loading) {
    el("rewrite-suggestions").innerHTML = '<div class="suggestions-loading">Preparing five source-backed choices…</div>';
    return;
  }
  if (error) {
    el("rewrite-suggestions").innerHTML = `<div class="suggestions-error">${escapeHtml(error)}</div>`;
    return;
  }
  el("rewrite-suggestions").innerHTML = state.rewriteSuggestions.length
    ? state.rewriteSuggestions.map((suggestion, index) => `
      <button type="button" class="rewrite-suggestion-choice" data-use-rewrite-suggestion="${index}">
        <span>${index + 1}</span><p>${escapeHtml(suggestion)}</p><strong>Use this</strong>
      </button>`).join("")
    : '<div class="suggestions-error">No new choices were returned.</div>';
}

async function loadRewriteSuggestions(instruction = "", excludeCurrent = true) {
  const target = state.rewriteTarget;
  if (!target) return;
  const lesson = state.plan.lessons[target.lessonIndex];
  state.rewriteSuggestionInstruction = instruction;
  renderRewriteSuggestions(true);
  try {
    const response = await fetch("/api/rewrite-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerConfig(),
        job_id: state.jobId,
        analysis: state.analysis,
        lesson,
        field: target.field,
        text: target.input.value,
        instruction,
        excluded: excludeCurrent ? state.rewriteSuggestions : []
      })
    });
    const data = await responseData(response);
    state.rewriteSuggestions = data.suggestions || [];
    renderRewriteSuggestions();
  } catch (error) {
    renderRewriteSuggestions(false, error.message);
  }
}

function useRewriteSuggestion(index) {
  const target = state.rewriteTarget;
  const suggestion = state.rewriteSuggestions[index];
  if (!target || !suggestion) return;
  target.input.value = suggestion;
  if (target.input.closest("#section-inspector")) syncInspector();
  else captureLessonEdits();
  markPreviewDirty();
  el("rewrite-dialog").close();
  toast("Suggestion applied. Refresh the Word preview when ready.");
}

async function applyRewrite() {
  const target = state.rewriteTarget;
  if (!target) return;
  const instruction = el("rewrite-instruction").value.trim();
  if (!instruction) return toast("Choose or enter a rewrite instruction.");
  const lesson = state.plan.lessons[target.lessonIndex];
  try {
    el("rewrite-dialog").close();
    busy("Rewriting one paragraph…", "Only the selected text will change.");
    const response = await fetch("/api/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerConfig(),
        job_id: state.jobId,
        analysis: state.analysis,
        lesson,
        text: target.input.value,
        instruction,
        context: { lesson_title: lesson.title, field: target.field, student_level: settings().student_level }
      })
    });
    const data = await responseData(response);
    target.input.value = data.text;
    if (target.input.closest("#section-inspector")) syncInspector();
    else captureLessonEdits();
    markPreviewDirty();
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

async function suggestImageForSection(lessonIndex, field) {
  const lesson = state.plan.lessons[lessonIndex];
  const selected = normalizedSelectedImages(lesson);
  if (selected.length >= 3 && !selected.some(item => item.field === field)) {
    return toast("The two-page template supports up to three images. Remove one first.");
  }
  try {
    busy("Asking AI to choose a source image…", `Checking figures against ${fieldLabels[field] || field}.`);
    const response = await fetch("/api/suggest-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerConfig(), job_id: state.jobId, field, lesson
      })
    });
    const data = await responseData(response);
    lesson.selected_images = [
      ...selected.filter(item => item.field !== field),
      { xref: Number(data.image.xref), field }
    ];
    syncSelectedImageXrefs(lesson);
    state.inspectorDirty = true;
    markPreviewDirty();
    renderInspector();
  } catch (error) { toast(error.message); }
  finally { idle(); }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function escapeAttribute(value) { return escapeHtml(value).replace(/\n/g, " "); }

el("provider").addEventListener("change", event => {
  renderModelOptions(event.target.value);
});
el("toggle-key").addEventListener("click", () => {
  const input = el("api-key");
  input.type = input.type === "password" ? "text" : "password";
  el("toggle-key").textContent = input.type === "password" ? "Show" : "Hide";
});
el("theory").addEventListener("input", event => {
  el("theory-value").textContent = `${event.target.value}%`;
  el("practical-value").textContent = `${100 - Number(event.target.value)}%`;
});
function clearSourceResults() {
  state.jobId = null;
  state.analysis = null;
  state.plan = null;
  state.images = [];
  state.availableTopics = [];
  state.emphasizedTopics = [];
  state.topicLibraryOpen = false;
  state.draggedTopicIndex = null;
  state.previewSelection = null;
  state.previewHotspots = [];
  el("module-summary").innerHTML = "";
  el("warning-list").innerHTML = "";
  el("topic-list").innerHTML = "";
  el("lesson-list").innerHTML = "";
  el("pdf-pages").innerHTML = '<div class="preview-placeholder">Generating the template preview…</div>';
  document.querySelectorAll('.step[data-step-target="2"], .step[data-step-target="3"]').forEach(step => {
    step.disabled = true;
    step.classList.remove("active", "complete");
  });
}

function showSelectedSource(file) {
  clearSourceResults();
  el("module-name").textContent = file?.name || "";
  el("uploaded-file-row").classList.toggle("hidden", !file);
  el("source-settings").classList.toggle("hidden", !file);
  el("analyze-button").disabled = !file;
  updateSourceReadControls(file);
}

function resetRestoredUploadState() {
  el("module-file").value = "";
  el("template-file").value = "";
  el("api-key").value = "";
  el("base-url").value = "";
  el("custom-model").value = "";
  el("source-type").value = "module";
  el("read-mode").value = "entire";
  el("page-from").value = "1";
  el("page-to").value = "";
  el("page-range-fields").classList.add("hidden");
  showSelectedSource(null);
}

function updateSourceReadControls(file) {
  const isPdf = Boolean(file && file.name.toLowerCase().endsWith(".pdf"));
  el("read-mode").disabled = Boolean(file) && !isPdf;
  if (file && !isPdf) {
    el("read-mode").value = "entire";
    el("page-range-fields").classList.add("hidden");
    el("read-mode-help").textContent = "Word documents are read in full because Word page breaks depend on fonts, printer, and layout settings.";
  } else {
    el("read-mode-help").textContent = isPdf ? "For a book, you can limit the AI to a specific physical page range." : "";
  }
}

function removeSelectedSource() {
  el("module-file").value = "";
  el("source-type").value = "module";
  el("read-mode").value = "entire";
  el("page-from").value = "1";
  el("page-to").value = "";
  el("page-range-fields").classList.add("hidden");
  showSelectedSource(null);
  setStep(1);
  toast("File removed. You can add another one now.");
}

el("module-file").addEventListener("change", event => showSelectedSource(event.target.files[0]));
el("remove-source").addEventListener("click", removeSelectedSource);
el("read-mode").addEventListener("change", event => {
  el("page-range-fields").classList.toggle("hidden", event.target.value !== "range");
});
el("template-file").addEventListener("change", event => { el("template-name").textContent = event.target.files[0]?.name || "No .docx selected"; });
el("toggle-topic-library").addEventListener("click", () => {
  syncTopicReviewFields();
  state.topicLibraryOpen = !state.topicLibraryOpen;
  renderAnalysis();
});
el("close-topic-library").addEventListener("click", () => {
  syncTopicReviewFields();
  state.topicLibraryOpen = false;
  renderAnalysis();
});
el("topic-list").addEventListener("click", event => {
  const emphasisButton = event.target.closest("[data-remove-emphasis]");
  if (emphasisButton) {
    syncTopicReviewFields();
    const index = Number(emphasisButton.dataset.removeEmphasis);
    const [removedEmphasis] = state.emphasizedTopics.splice(index, 1);
    if (removedEmphasis) state.availableTopics.push(removedEmphasis);
    renderAnalysis();
    return;
  }
  const button = event.target.closest("[data-remove-topic]");
  if (!button) return;
  syncTopicReviewFields();
  const index = Number(button.dataset.removeTopic);
  const [removed] = state.analysis.topics.splice(index, 1);
  if (removed) {
    const removedKey = normalizedTopicTitle(removed.title);
    const promoted = state.emphasizedTopics.filter(topic => normalizedTopicTitle(topic.parent_title) === removedKey);
    state.emphasizedTopics = state.emphasizedTopics.filter(topic => normalizedTopicTitle(topic.parent_title) !== removedKey);
    if (promoted.length) state.analysis.topics.splice(index, 0, ...promoted.map(topic => ({ ...topic, parent_title: "" })));
    state.availableTopics.push(removed);
    state.availableTopics.sort((a, b) => Number(a.start_page) - Number(b.start_page) || Number(a.end_page) - Number(b.end_page));
  }
  renderAnalysis();
});
el("candidate-list").addEventListener("click", event => {
  const emphasizeButton = event.target.closest("[data-emphasize-topic]");
  if (emphasizeButton) {
    syncTopicReviewFields();
    const index = Number(emphasizeButton.dataset.emphasizeTopic);
    const [emphasized] = state.availableTopics.splice(index, 1);
    if (emphasized) state.emphasizedTopics.push(emphasized);
    renderAnalysis();
    return;
  }
  const button = event.target.closest("[data-add-topic]");
  if (!button) return;
  syncTopicReviewFields();
  const index = Number(button.dataset.addTopic);
  const [added] = state.availableTopics.splice(index, 1);
  if (added) state.analysis.topics.push(added);
  renderAnalysis();
});
el("topic-list").addEventListener("dragstart", event => {
  const card = event.target.closest("[data-topic-index]");
  if (!card) return;
  syncTopicReviewFields();
  state.draggedTopicIndex = Number(card.dataset.topicIndex);
  card.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(state.draggedTopicIndex));
});
el("topic-list").addEventListener("dragover", event => {
  if (state.draggedTopicIndex === null) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  document.querySelectorAll("#topic-list .topic-card").forEach(card => card.classList.remove("drag-target"));
  event.target.closest("[data-topic-index]")?.classList.add("drag-target");
});
el("topic-list").addEventListener("drop", event => {
  event.preventDefault();
  const target = event.target.closest("[data-topic-index]");
  const from = state.draggedTopicIndex;
  if (!target || from === null) return;
  const to = Number(target.dataset.topicIndex);
  const [moved] = state.analysis.topics.splice(from, 1);
  if (moved) state.analysis.topics.splice(to, 0, moved);
  state.draggedTopicIndex = null;
  renderAnalysis();
});
el("topic-list").addEventListener("dragend", () => {
  state.draggedTopicIndex = null;
  document.querySelectorAll("#topic-list .topic-card").forEach(card => card.classList.remove("dragging", "drag-target"));
});
el("analyze-button").addEventListener("click", analyzeModule);
el("generate-button").addEventListener("click", generatePlans);
el("export-word-button").addEventListener("click", exportPlans);
el("pdf-pages").addEventListener("click", event => {
  const hotspot = event.target.closest("[data-preview-field]");
  if (!hotspot) return;
  if (hotspot.dataset.dragged === "true") {
    hotspot.dataset.dragged = "false";
    return;
  }
  selectPreviewField(Number(hotspot.dataset.lessonIndex), hotspot.dataset.previewField);
});
el("pdf-pages").addEventListener("pointerdown", startImageDrag);
el("pdf-pages").addEventListener("pointermove", moveImageDrag);
el("pdf-pages").addEventListener("pointerup", finishImageDrag);
el("pdf-pages").addEventListener("pointercancel", finishImageDrag);
el("section-inspector").addEventListener("input", event => {
  if (event.target.matches("[data-edit-text]")) syncInspector();
});
el("section-inspector").addEventListener("change", event => {
  if (event.target.matches("[data-format-size]")) applyParagraphFontSize(event.target);
});
el("section-inspector").addEventListener("change", event => {
  const pick = event.target.closest("[data-pick-image]");
  if (!pick) return;
  state.imageDownloadPicks ||= new Set();
  const name = pick.dataset.pickImage;
  if (pick.checked) state.imageDownloadPicks.add(name);
  else state.imageDownloadPicks.delete(name);
  syncImagePickControls();
});
el("section-inspector").addEventListener("click", event => {
  const selection = state.previewSelection;
  if (!selection) return;
  const lesson = state.plan.lessons[selection.lessonIndex];
  const section = event.target.closest(".list-edit[data-field]");
  const formatControl = event.target.closest("[data-format-action]");
  if (formatControl) return applyParagraphFormatControl(formatControl);
  if (event.target.closest("[data-rewrite-item]")) return openRewrite(event.target.closest("[data-rewrite-item]"));
  if (event.target.closest("[data-inspector-refresh]")) return refreshPreview();
  if (event.target.closest("[data-select-all-images]")) {
    const boxes = [...document.querySelectorAll("[data-pick-image]")];
    const selectAll = boxes.some(box => !box.checked);
    state.imageDownloadPicks ||= new Set();
    boxes.forEach(box => {
      box.checked = selectAll;
      if (selectAll) state.imageDownloadPicks.add(box.dataset.pickImage);
      else state.imageDownloadPicks.delete(box.dataset.pickImage);
    });
    return syncImagePickControls();
  }
  if (event.target.closest("[data-download-images]")) {
    const picks = [...(state.imageDownloadPicks || [])];
    if (!picks.length) return toast("Tick the images you want first.");
    // A plain link download: the browser saves them as one zip the teacher can unpack.
    const url = `/api/jobs/${encodeURIComponent(state.jobId || "")}/images.zip?names=${encodeURIComponent(picks.join(","))}`;
    const link = document.createElement("a");
    link.href = url;
    link.download = "source-images.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    return;
  }
  if (event.target.closest("[data-add-section-point]")) {
    syncInspector();
    // The teacher decides how many points a section carries. The two-page render check before
    // download is the real gate, so adding one more is never blocked here.
    lesson[selection.field].push("");
    lesson.text_formatting ||= {};
    lesson.text_formatting[selection.field] ||= [];
    lesson.text_formatting[selection.field].push({});
    state.inspectorDirty = true;
    return renderInspector();
  }
  const quickSectionPrompt = event.target.closest("[data-section-ai-prompt]");
  if (quickSectionPrompt) return reviseWholeSection(quickSectionPrompt.dataset.sectionAiPrompt);
  if (event.target.closest("[data-run-section-ai]")) {
    const instruction = el("section-ai-instruction")?.value.trim();
    if (!instruction) return toast("Tell the AI what to change across this section.");
    return reviseWholeSection(instruction);
  }
  if (event.target.closest("[data-discard-section-proposal]")) {
    state.sectionProposal = null;
    return renderInspector();
  }
  if (event.target.closest("[data-apply-section-proposal]")) {
    if (!state.sectionProposal) return;
    lesson[selection.field] = [...state.sectionProposal.items];
    lesson.text_formatting ||= {};
    const existingFormats = lesson.text_formatting[selection.field] || [];
    lesson.text_formatting[selection.field] = lesson[selection.field].map((_, index) => existingFormats[index] || {});
    state.sectionProposal = null;
    state.inspectorDirty = true;
    markPreviewDirty();
    return renderInspector();
  }
  if (event.target.closest("[data-add-line]") && section) {
    syncInspector();
    if (!Array.isArray(lesson[selection.field])) lesson[selection.field] = [];
    lesson[selection.field].push("");
    lesson.text_formatting ||= {};
    lesson.text_formatting[selection.field] ||= [];
    lesson.text_formatting[selection.field].push({});
    state.inspectorDirty = true;
    return renderInspector();
  }
  if (event.target.closest("[data-delete-item]") && section) {
    const itemIndex = Number(event.target.closest(".editable-item").dataset.itemIndex || 0);
    syncInspector();
    if (section.hasAttribute("data-single-field")) lesson[selection.field] = "";
    else {
      lesson[selection.field].splice(itemIndex, 1);
      lesson.text_formatting?.[selection.field]?.splice(itemIndex, 1);
    }
    state.inspectorDirty = true;
    markPreviewDirty();
    return renderInspector();
  }
  const removeImage = event.target.closest("[data-remove-image]");
  if (removeImage) {
    lesson.selected_images = normalizedSelectedImages(lesson).filter(item => !(
      Number(item.xref) === Number(removeImage.dataset.removeImage)
      && item.field === removeImage.dataset.imageField
    ));
    syncSelectedImageXrefs(lesson);
    state.inspectorDirty = true;
    markPreviewDirty();
    return renderInspector();
  }
  const addImage = event.target.closest("[data-add-image]");
  if (addImage) {
    const selected = normalizedSelectedImages(lesson);
    const field = addImage.dataset.imageField;
    if (selected.some(item => item.field === field)) return toast("Remove this section's current image before adding another.");
    if (selected.length >= 3) return toast("The two-page template supports up to three images. Remove one first.");
    lesson.selected_images = [...selected, { xref: Number(addImage.dataset.addImage), field }];
    syncSelectedImageXrefs(lesson);
    state.inspectorDirty = true;
    markPreviewDirty();
    return renderInspector();
  }
  const suggestImage = event.target.closest("[data-suggest-image]");
  if (suggestImage) return suggestImageForSection(selection.lessonIndex, suggestImage.dataset.imageField);
});
el("lesson-list").addEventListener("click", event => {
  const card = event.target.closest("[data-lesson-card]");
  if (!card) return;
  const lessonIndex = Number(card.dataset.lessonCard);
  const lesson = state.plan.lessons[lessonIndex];
  const section = event.target.closest(".list-edit[data-field]");
  const formatControl = event.target.closest("[data-format-action]");
  if (formatControl) return applyParagraphFormatControl(formatControl);
  if (event.target.closest("[data-rewrite-item]")) return openRewrite(event.target.closest("[data-rewrite-item]"));
  if (event.target.closest("[data-add-line]") && section) {
    captureLessonEdits();
    lesson[section.dataset.field].push("");
    lesson.text_formatting ||= {};
    lesson.text_formatting[section.dataset.field] ||= [];
    lesson.text_formatting[section.dataset.field].push({});
    return renderLessons(lessonIndex);
  }
  if (event.target.closest("[data-delete-item]") && section) {
    captureLessonEdits();
    if (section.hasAttribute("data-single-field")) lesson[section.dataset.field] = "";
    else {
      const itemIndex = Number(event.target.closest(".editable-item").dataset.itemIndex);
      lesson[section.dataset.field].splice(itemIndex, 1);
      lesson.text_formatting?.[section.dataset.field]?.splice(itemIndex, 1);
    }
    return renderLessons(lessonIndex);
  }
  const removeImage = event.target.closest("[data-remove-image]");
  if (removeImage) {
    captureLessonEdits();
    lesson.selected_images = normalizedSelectedImages(lesson).filter(item => !(
      Number(item.xref) === Number(removeImage.dataset.removeImage)
      && item.field === removeImage.dataset.imageField
    ));
    syncSelectedImageXrefs(lesson);
    markPreviewDirty();
    return renderLessons(lessonIndex);
  }
  const addImage = event.target.closest("[data-add-image]");
  if (addImage) {
    captureLessonEdits();
    const selected = normalizedSelectedImages(lesson);
    const field = addImage.dataset.imageField;
    if (selected.some(item => item.field === field)) return toast("Remove this section's current image before adding another.");
    if (selected.length >= 3) return toast("The two-page template supports up to three images. Remove one first.");
    lesson.selected_images = [...selected, { xref: Number(addImage.dataset.addImage), field }];
    syncSelectedImageXrefs(lesson);
    markPreviewDirty();
    return renderLessons(lessonIndex);
  }
});
el("lesson-list").addEventListener("input", event => {
  if (event.target.matches("textarea, input")) markPreviewDirty();
});
el("lesson-list").addEventListener("change", event => {
  if (event.target.matches("[data-format-size]")) applyParagraphFontSize(event.target);
});
document.querySelectorAll("[data-rewrite-prompt]").forEach(button => button.addEventListener("click", () => {
  el("rewrite-instruction").value = button.dataset.rewritePrompt;
}));
el("rewrite-suggestions").addEventListener("click", event => {
  const choice = event.target.closest("[data-use-rewrite-suggestion]");
  if (choice) useRewriteSuggestion(Number(choice.dataset.useRewriteSuggestion));
});
el("refresh-rewrite-suggestions").addEventListener("click", () => {
  loadRewriteSuggestions(state.rewriteSuggestionInstruction, true);
});
el("generate-rewrite-suggestions").addEventListener("click", () => {
  const instruction = el("rewrite-instruction").value.trim();
  if (!instruction) return toast("Enter what you want the five choices to focus on.");
  loadRewriteSuggestions(instruction, true);
});
el("apply-rewrite").addEventListener("click", applyRewrite);
el("refresh-preview").addEventListener("click", () => refreshPreview());
el("preview-zoom").addEventListener("change", applyPreviewZoom);
document.querySelectorAll("[data-go-step]").forEach(button => button.addEventListener("click", () => setStep(Number(button.dataset.goStep))));
document.querySelectorAll(".step").forEach(button => button.addEventListener("click", () => { if (!button.disabled) setStep(Number(button.dataset.stepTarget)); }));

const drop = el("module-drop");
["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.add("dragging"); }));
["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.remove("dragging"); }));
drop.addEventListener("drop", event => {
  const file = event.dataTransfer.files[0];
  if (file && /\.(pdf|docx|doc)$/i.test(file.name)) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    el("module-file").files = transfer.files;
    showSelectedSource(file);
  } else toast("Please drop a PDF or Word file (.pdf, .docx, or .doc)." );
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    state.config = await responseData(response);
    el("template-name").textContent = state.config.default_template_available
      ? `Built in: ${state.config.default_template_name}`
      : "Choose a .docx template";
  } catch (_) {
    el("template-name").textContent = "Choose a .docx template";
  }
}

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

// A native date input renders in the browser's locale, so a teacher typing day-first into a
// mm/dd/yyyy field silently stores the wrong date. Separate fields with a named month remove
// every chance of day and month being swapped.
function daysInMonth(month, year) {
  return new Date(year, month, 0).getDate();
}

function syncStartDate() {
  const day = Number(el("start-day").value);
  const month = Number(el("start-month").value);
  const year = Number(el("start-year").value);
  const echo = el("start-date-echo");
  if (!day || !month || !year || year < 1000) {
    el("start-date").value = "";
    if (echo) echo.textContent = "Enter the day, month, and year.";
    return;
  }
  const maxDay = daysInMonth(month, year);
  if (day > maxDay) {
    el("start-date").value = "";
    if (echo) echo.textContent = `${MONTH_NAMES[month - 1]} ${year} only has ${maxDay} days.`;
    return;
  }
  el("start-date").value = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  if (echo) echo.textContent = `Starts ${day} ${MONTH_NAMES[month - 1]} ${year}`;
}

el("start-month").innerHTML = MONTH_NAMES
  .map((name, index) => `<option value="${index + 1}">${name}</option>`).join("");
["start-day", "start-month", "start-year"].forEach(id => {
  el(id).addEventListener("change", syncStartDate);
  el(id).addEventListener("input", syncStartDate);
});
const today = new Date();
el("start-day").value = today.getDate();
el("start-month").value = today.getMonth() + 1;
el("start-year").value = today.getFullYear();
syncStartDate();
let openRouterAuto = null;

async function loadOpenRouterAuto() {
  if (openRouterAuto) return openRouterAuto;
  const response = await fetch("/api/openrouter-models");
  if (!response.ok) throw new Error("model list unavailable");
  const data = await response.json();
  openRouterAuto = data.auto || [];
  return openRouterAuto;
}

function updateModelHelp() {
  const provider = el("provider").value;
  const choice = (providerModels[provider] || []).find(([value]) => value === el("model").value);
  el("model-help").textContent = choice?.[2] || "";
}

function renderModelOptions(provider = el("provider").value) {
  const choices = providerModels[provider] || [];
  const automatic = provider === "openrouter";
  el("model").innerHTML = choices.map(([value, label]) => `<option value="${escapeAttribute(value)}">${escapeHtml(label)}</option>`).join("");
  el("model").classList.toggle("hidden", provider === "custom" || automatic);
  el("model-wrap").classList.toggle("hidden", automatic);
  el("custom-model-wrap").classList.toggle("hidden", provider !== "custom");
  el("base-url-wrap").classList.toggle("hidden", provider !== "custom");
  el("auto-model-note").classList.toggle("hidden", !automatic);
  el("model-help").textContent = choices[0]?.[2] || "Use the exact model name supplied by your provider.";

  if (!automatic) return;
  el("auto-model-detail").textContent = "Checking which free models are available right now…";
  loadOpenRouterAuto()
    .then(list => {
      if (el("provider").value !== "openrouter") return;
      el("auto-model-detail").textContent = list.length
        ? `${list.length} free models are ready. LessonFlow starts with the strongest and switches to the next one by itself if a model is busy or answers badly.`
        : "LessonFlow will pick a free model automatically.";
    })
    .catch(() => {
      if (el("provider").value !== "openrouter") return;
      el("auto-model-detail").textContent = "LessonFlow will pick a free model automatically.";
    });
}

el("model").addEventListener("change", updateModelHelp);
renderModelOptions();
loadConfig();
window.addEventListener("pageshow", resetRestoredUploadState);
resetRestoredUploadState();
