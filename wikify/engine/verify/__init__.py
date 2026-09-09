"""Page-scoring engine — ported from the POC `verify/` package.

`deterministic` (no LLM), `judge` (LLM-as-judge over OpenRouter), and `harness`
(combine into a page-type-aware `PageScore`). Logic is unchanged from the POC; only
the I/O boundaries move (judge calls `engine.llm`, thresholds come from
`engine.settings`, the page image arrives as a data URL instead of a path).
"""

from __future__ import annotations

from wikify.engine.verify.harness import PageScore, get_verdict, score_page

__all__ = ["PageScore", "get_verdict", "score_page"]
