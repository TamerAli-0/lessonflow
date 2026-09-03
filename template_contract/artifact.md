# Retained lesson-plan template contract

## Reference

- Source: `assets/lesson-plan-template.docx`
- SHA-256: `e9d6f89b5d9004a11c2c2473bb350eaba9a6fbe4fb0e5577730649099d026157`
- Rendered page count: 6
- Word sections: 1
- Main content: two top-level tables; the first contains metadata and six lesson stages, the second contains reflection.

The source is read-only design authority. Exports are made from a copy.

## Page system

- A4 landscape: 11.69 × 8.27 inches.
- Margins: left 1.00, right 1.44, top 0.37, bottom 0.69 inches.
- One `NEW_PAGE` section, different first page enabled.
- Three PAGE fields in footer parts. Existing header/footer parts and relationships are preserve-only.

## Typography and visuals

- Dominant font: Arial Narrow, with extensive direct run and paragraph formatting.
- Table geometry, borders, fills, vertical labels, red instructional labels, and red reflection treatment are preserve-only.
- The template contains 11 anchored images and one inline header image. The header image is preserve-only. Old lesson-specific images in dynamic activity cells are removed when image extraction is off. When it is on, the exporter reuses each selected stage's floating anchor and swaps in a figure from the module's physical PDF page range. Up to three stages may each carry one image.
- User-moved images store continuous x/y ratios constrained only by their target Word cell. Word tight wrapping remains active; text in a section compacts locally as an image moves farther into its text area.
- Existing numbering, styles, theme, headers, footers, page fields, ink parts, web settings, and custom XML are preserve-only.

## Semantic slot map

Locators use normalized semantic labels, not row numbers or source lesson prose.

- Metadata neighbor slots: `Course`, `Class`, `Week & Date`.
- Topic overview slot: cell containing both `Lesson/unit/topic & pages` and `resources`; replace the module/chapter value and the value immediately following each label.
- Summary slots: preserve the first heading paragraph in cells containing `Main learning focus (KPIs)`, `C21st Skills`, `Success Criteria`, and `Numeracy, Literacy, Culture & Heritage`; replace subsequent content.
- Stage slots: locate label cells containing `WARM UP`, `FOCUSED INSTRUCTION`, `GUIDED INSTRUCTION`, `COLLABORATIVE LEARNING`, `INDEPENDENT LEARNING`, and `PROGRESS CHECK`. Where a stage spans a prompt row and an activity row, choose the right-hand cell with the larger existing content payload. Replace that cell only; preserve the label and red instructional prompt cells.
- Time slots: the unique cell immediately left of each selected stage-label cell.
- Homework slot: extra right-hand cell containing `Home Learning` on the progress-check activity row; preserve its heading and replace subsequent content.
- Reflection slot: last row of the separate table containing both `Reflection` and `Recap success criteria`.

## Capacity rules

- Content is deliberately concise and emitted as separate paragraphs.
- Keep cell widths fixed. Generated body text may auto-fit through 10, 9.25, and 8.5 pt steps; never shrink below 8.5 pt. Dynamic stage rows use automatic height.
- Pagination remains natural so dense modules do not create isolated pages. The opening-stage header is kept with Warm Up, activity rows cannot split internally, and minimum row heights are cleared in copied output.
- One exported document represents the complete uploaded module plan. Module weeks and sessions affect pacing inside that single template rather than multiplying documents.
- If a required semantic locator is absent, fail with a clear template error rather than writing to guessed coordinates.

## Fidelity gates

- Source hash must remain unchanged.
- Export must retain landscape A4 geometry, section count, tables, headers, footers, PAGE fields, and fixed stage labels.
- Render every page of representative image-off and image-on exports and inspect for clipped text, displaced anchored images, broken vertical labels, orphaned reflection rows, blank pages, and unexpected pagination.
- Compare package inventories; any loss of preserve-only parts or relationships is a failure.
