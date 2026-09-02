# Visual fidelity — diagrams, tables & figures (ICAI / exam-prep track)

Motivating document: `86805bos-aps1326-final-slmr-p4.pdf` — ICAI Final Paper 4, "Last Mile
Referencer", 236 pages. This is CA revision material: almost entirely flowcharts, rate
tables and mind-maps. Target user: a student revising for the exam, where a **wrong number
is worse than a missing one**.

## Measured diagnosis (all figures verified against the real PDF and the parsed rows)

| Signal | Value |
|---|---|
| Pages | 236 |
| Median text chars / page | **1,603** (full text layer — it is NOT a scanned PDF) |
| Median vector drawings / page | **150.5** (max 898) |
| Pages with >40 drawings | **193 / 236** |
| Pages with embedded raster images | 61 |
| Pages classified `kind="visual"` | **1** |
| Pages with verdict `review` | **206 / 236** |
| Mean composite score | 0.631 |
| Pages emitting a mermaid block | 38 |
| Pages emitting a pipe table | 147 |

### Failure 1 — visual detection can never fire on this document
`engine/config.py` gates the visual path on `chars < visual_min_chars (250)` **AND**
`drawings > visual_min_drawings (40)`. ICAI pages have both a full text layer *and* heavy
vector art, so **zero** pages satisfy the conjunction. The rule was written for scanned,
text-free pages; a born-digital diagram-heavy PDF is invisible to it. The 98 pages that did
reach the VLM got there accidentally, via the low-composite remediation fallback.

### Failure 2 — mermaid is being used to encode TABLES, which corrupts the data
Verified on p.11 (a surcharge rate table, 467 drawings). The VLM emitted:

```
F --> F1["(i) TI ... > ₹ 50 lakhs but ≤ ₹ 1 crore"]  … F5
G --> G1["10%"] G2["15%"] G3["25%"] …
```

Two **parallel dangling branches** — slabs under `F`, rates under `G` — with no edge
binding a slab to its rate. The row↔rate correspondence is destroyed. A student reading
this gets the surcharge rates wrong. This is not "poor quality output", it is
**confidently wrong output**, which is the worst failure mode for exam prep.

Root cause: a flowchart grammar is being asked to represent a 2-D grid. Mermaid is right
for genuine process/decision flows and wrong for tabular data. Nothing routes by shape.

### Failure 3 — mermaid never renders in the browser anyway
`frontend/src/utils/mermaid.js` loads `/assets/wiki/js/mermaid-loader.js` and expects
`window.wikiGetMermaid`. **Neither exists in the installed wiki app.** Verified:
`mermaid-loader.js` is absent from `sites/assets/wiki/js/` and
`vendor/mermaid/mermaid.min.js` returns **404**; the wiki app ships mermaid only as a
Vite-hashed chunk inside its own SPA bundle (`mermaid-mWjccvbQ.js`). The util degrades
silently to plain code blocks exactly as its own comment promises. So even correct mermaid
would render as raw text.

## Prior art worth adopting

- **Azure Document Intelligence / AWS Textract** — table structure as first-class typed
  output (cells with row/col spans), never markdown pipes.
- **IBM Docling (TableFormer), Reducto, LlamaParse, Unstructured** — complex tables are
  emitted as **HTML**, because HTML supports `rowspan`/`colspan` and markdown does not.
  ICAI tables are full of merged headers; pipe tables structurally cannot hold them.
- **Multimodal RAG norm (Anthropic cookbook, LlamaIndex)** — keep the cropped page region
  as an image, generate a text description for retrieval, and show the **original crop** to
  the user. The image is the ground truth; the text is the index.
- **Nougat / Marker** — LaTeX for formulae rather than lossy plain text.

## Tasks

### A. Make the browser render diagrams at all
1. Vendor `mermaid.min.js` into `wikify/public/js/vendor/` (do not depend on the wiki app's private bundle).
2. Rewrite `frontend/src/utils/mermaid.js` to load our own asset; keep the wiki-loader path only as an optional fast path.
3. Add a visible **render-failure state**: invalid mermaid shows the diagram source + an error chip, never silence.
4. Screenshot proof: an ICAI page rendering a real flowchart in the SPA preview.

### B. Route content by shape instead of forcing everything through mermaid
5. `engine/` classifier: per-region intent — `table` | `flow` | `figure` | `prose`.
6. Tables → **HTML `<table>`** with `rowspan`/`colspan`; never a flowchart. Pipe tables only for genuinely flat grids.
7. Flows/decision trees → mermaid (its actual job).
8. Decorative/complex figures → cropped page image + caption.
9. **Validate mermaid server-side before storing** — parse it; on failure fall back to table or crop rather than persisting broken syntax.
10. Golden test on p.11: assert every income slab is bound to exactly one surcharge rate (this is the correctness bar).

### C. Fix visual detection
11. Replace the `AND` gate with an **ink-density / drawing-density** score; a page with 150 drawings routes to the visual path regardless of its text layer.
12. Classify per-region, not per-page — ICAI pages are mixed prose + diagram.
13. Re-run the 236-page parse and compare composite scores before/after.

### D. Preserve ground truth
14. Always crop and store the source region image alongside generated markdown.
15. Wiki output shows the crop next to the rendered diagram, so a student can verify against the original.
16. Retrieval indexes the description; the citation shows the image.

## Exam-prep specifics (this corpus, not generic)
17. Preserve `₹`, section references (`u/s 115BAC`), and provisos verbatim — never paraphrase a statutory rate.
18. Formulae → LaTeX rather than flattened text.
19. Numeric-fidelity check: every percentage and threshold in the source must appear in the output.
