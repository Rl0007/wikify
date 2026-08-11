# RAG latency & context-size prior art — what production systems actually do

**Date of research:** 2026-08-11. **Scope:** how production RAG/answer systems handle the latency and
context-size problem we have measured in Wikify's Ask pipeline, and what is genuinely worth adopting.

**Our measured baseline** (the numbers everything below is judged against):

| Stage | Wall clock | Share |
|---|---|---|
| Synthesis LLM (claude-sonnet-4.6, ~12k-token prompt) | 13.7s | 55% |
| Retrieval + rerank (almost entirely the LLM rerank calls) | 8.9s | 36% |
| Intent router LLM | 2.1s | 8% |
| Evidence verification | 13ms | ~0% |
| Vector DB | 12–23ms | ~0% |
| **Total** | **~25s** | **$0.047/question**, synthesis ≈92% of cost |

Rerank: 50 candidates → 8, LLM reranker (`google/gemini-2.5-flash`), batches of 10, only 1.65× speedup
from 4-way threading. Synthesis prompt 55,049 chars because parent-document retrieval sends whole
sections (8 hits × 4,524 chars avg). Corpus: 2 ICAI study PDFs (236pp + 180pp), ~1,150 chunks.
Constraint: **a wrong statutory rate is disqualifying; slow is merely annoying.**

---

## Reading conventions

Throughout, claims are tagged:

- **[M]** — a number **measured and published in the cited source**.
- **[V]** — a **vendor marketing** claim, no published methodology.
- **[I]** — **my inference**, not in any source.
- **[?]** — source found but I could **not independently verify** the specific figure; treat as weak.

Publication dates are given for everything. This field moves fast: anything older than ~18 months
(i.e. pre-2025) is flagged where its age might matter.

---

## 0. What we do today vs what production systems do

| Dimension | Wikify today | Production norm | Evidence strength |
|---|---|---|---|
| **Rerank model** | LLM (gemini-2.5-flash), listwise, batches of 10 | Dedicated cross-encoder API or self-hosted (Cohere Rerank 4, Voyage rerank-2.5, Jina v3.5, mxbai-rerank-v2) | Strong, multiple independent [M] |
| **Rerank latency** | **8.9s** | **130ms – 1s** for 50–100 docs | Strong [M] (Jina, Oracle/Cohere, ZeroEntropy, mixedbread) |
| **Rerank depth** | 50 → 8 | 30–100 → 5–20; depth 50 is fine and well-supported | Strong [M] (Elastic BEIR sweep, Anthropic) |
| **Retrieved unit sent to LLM** | Whole parent sections, unconditionally, ~1,100 tok each | Capped parents (LangChain `parent_splitter`), gated merging (LlamaIndex ≥n children), or small contextualised chunks (Anthropic) | Strong — **nobody recommends unconditional whole-section expansion** |
| **Context budget** | ~12k tokens | ~4k–8k total is the convergent recommendation; more, smaller units beats fewer, larger | Medium [M], sources disagree on granularity |
| **Intent routing** | LLM call, **2.1s** | TF-IDF+SVM (sub-ms), embedding router (single-digit ms), DistilBERT (p50 9ms), or **no router at all** (fuse both modes) | Strong [M] + [?] on 2026 preprints |
| **Pipeline shape** | 3 sequential LLM calls | Merge prompts, parallelise non-dependent steps, speculatively start retrieval; keep the LLM out of the request path where possible | Strong — OpenAI's own latency guide names our anti-pattern |
| **Streaming** | None (blocking 25s) | Streaming-first is universal | Strong |
| **Retrieval latency budget** | 12–23ms (fine) | 200ms–2s published targets (Vespa, Dropbox Dash, Exa) | Our vector store is a non-issue |
| **Caching** | None | Provider prefix/KV caching yes; semantic caching largely not, for RAG | Medium [M] |
| **Numeric safety** | Evidence verification (13ms) | No vendor solves exact-figure verification; deterministic renderer-side checking is the published design (PCN) | Weak literature, strong argument |

**The one-line summary:** our vector store is irrelevant, our rerank step is ~15–60× slower than the
published state of the art at *equal or better quality*, our router should not be an LLM, and our
synthesis is **decode-bound, not prefill-bound** — which means cutting the 12k-token prompt is a cost
fix, not a speed fix. That last point inverts the intuition the task brief was built on and is the
most important finding in this document.

---

## 1. Reranking

### 1.1 Is LLM-as-reranker standard? No — it is the thing vendors and papers argue against

The clearest and most directly comparable study is Voyage AI's **"The Case Against LLMs as Rerankers"**
(2025-10-22, https://blog.voyageai.com/2025/10/22/the-case-against-llms-as-rerankers/). I fetched and
verified this myself. Setup: 13 real-world datasets, 8 domains, sliding-window listwise reranking with
**window size 20**, LLMs prompted "following RankLLM" with structured JSON output — i.e. structurally
the same architecture as ours.

**[M]** Average NDCG@10:

| System | NDCG@10 |
|---|---|
| `rerank-2.5` | **84.32%** |
| `rerank-2.5-lite` | 83.12% |
| GPT-5 | ~71.71% (−12.61) |
| Gemini 2.5 Pro | ~70.89% (−13.43) |
| Qwen3-32B | ~69.54% (−14.78) |

**[M]** Speed: `rerank-2.5` is *"9x, 36x, and 48x faster than Claude Sonnet 4.5, GPT-5, and Gemini 2.5
Pro, respectively."* **[M]** Cost: $0.05/MTok (`rerank-2.5`), $0.02 (`lite`), vs $1.25–$3 for the LLMs
— 25–60× more expensive.

This is a vendor arguing for its own product, so discount the magnitude. But the direction is
corroborated independently:

- **[M] Set-Encoder** (arXiv:2404.06912, ECIR 2025, rev. 2025-04-05): **RankZephyr is ~110× slower
  than monoELECTRA-Large at comparable effectiveness**; a 330M Set-Encoder is ~85× faster than
  windowed RankGPT-4o.
- **[M] ZeroEntropy** (2025-09-05,
  https://zeroentropy.dev/articles/should-you-use-llms-for-reranking-a-deep-dive-into-pointwise-listwise-and-cross-encoders/),
  17 benchmarks, p50 latency on 75kb input — **the cross-encoder was both most accurate AND 5–17×
  faster than every LLM reranker tested**:

  | Model | NDCG@10 | p50 latency | $/M input tok |
  |---|---|---|---|
  | zerank-1 (cross-encoder) | **0.7767** | **129.7ms** | $0.025 |
  | Cohere Rerank-3.5 | 0.7194 | 198.1ms | $0.050 |
  | GPT-4.1-mini | 0.7131 | 740ms | $0.80 |
  | GPT-5-nano | 0.7116 | 1,520ms | $0.050 |
  | GPT-5-mini | 0.6980 | 2,180ms | $0.250 |

  (ZeroEntropy sells a reranker — self-interested, but the numbers are specific and the setup is
  disclosed. Their earlier 2025-07-20 post honestly reports the *opposite* quality result in one
  configuration: listwise Gemini Flash beat BGE-reranker-v2 0.78 vs 0.74 NDCG@10 — **at 420ms vs 12ms
  and $18 vs $2 per 1k queries.** So: LLM listwise *can* win slightly on quality; it always loses
  catastrophically on latency and cost.)
- **[M] Anthropic's own Contextual Retrieval reference pipeline** (2024-09-19,
  https://www.anthropic.com/news/contextual-retrieval) uses **the Cohere reranker**, not an LLM.

### 1.2 What latency do dedicated rerankers actually achieve?

The most directly comparable figures to our 8.9s:

| Source | Workload | Latency |
|---|---|---|
| **Jina** (https://jina.ai/reranker/, jina-reranker-v3 rel. 2025-10-03) **[V, but specific]** | 100 docs × 256 tok, 64-tok query | **~150ms** |
| Same page | 100 docs × **4096 tok**, 512-tok query | **~7s** — *document length dominates, not count* |
| **Oracle OCI benchmark of Cohere Rerank 3.5** (https://docs.oracle.com/en-us/iaas/Content/generative-ai/benchmark-cohere-rerank-3-5.htm) **[M, third-party]** | 96 docs × 512 tok | **0.54s** |
| Same | 96 docs × 256 tok | 0.28s |
| Same | 96 docs × 2048 tok | 3.77s |
| **Elastic measuring Jina v3.5 vs v3** (2026-07-27, https://www.elastic.co/search-labs/blog/jina-reranker-35-legal-medical-structured-data) **[M]** | BEIR NQ, 145 tok avg docs | v3 371.1ms → v3.5 305.3ms |
| Same | AILAcasedocs, 1,904 tok avg docs | v3 16.1s → v3.5 10.3s |
| **mixedbread** (2025-03-13, https://www.mixedbread.com/blog/mxbai-rerank-v2) **[V, self-benchmark, A100 80GB, NFCorpus]** | doc count not stated | base-v2 (0.5B) **0.67s**; large-v2 (1.5B) 0.89s; **bge-reranker-v2-gemma (2.5B) 7.20s** |
| **FlashRank on CPU** (https://github.com/clouatre-labs/rag-reranking-benchmarks, 2026) **[M]** | 16 candidates → 8, ms-marco-MiniLM-L-12-v2 (34MB), MacBook Pro CPU | **+31.3ms overhead** (79.1ms vs 47.8ms without), 480 timed queries |

**The load-bearing caveat for us: document length, not document count, is what makes reranking slow.**
Our chunks average 4,524 chars ≈ 1,100 tokens. Reading Oracle's table, 96 docs at 1024 tok = 1.38s and
at 2048 tok = 3.77s. **[I]** So 50 docs at ~1,100 tokens with a dedicated reranker is plausibly
**0.5–1.0s**, not 150ms. That is still a **9–18× improvement** on our 8.9s, but I would not promise
sub-200ms without measuring on our actual chunk lengths.

**Two negative findings worth internalising:**

1. **"Cross-encoder" does not automatically mean fast.** `bge-reranker-v2-gemma` (2.5B, LLM backbone)
   measured **7.20s** — as slow as our current setup. The win comes from small encoder backbones
   (0.5B and below), not from the "cross-encoder" label.
2. **Cohere publishes no latency figures at all.** The "Rerank 4 Fast is optimized for low latency"
   framing (https://cohere.com/blog/rerank-4, 2025-12-11) is marketing without numbers. Voyage
   publishes none either. The only real numbers are third-party (Oracle, Elastic) or from vendors
   selling *against* the incumbent.

**Current lineup, verified from docs:** Cohere `rerank-v4.0-pro` / `rerank-v4.0-fast` (32k context,
32,764-tok chunks, max 10,000 docs/request; rerank-3.5 deprecated 2026-07-01 **[?]** — I did not
confirm the deprecation date on a Cohere page). Voyage `rerank-2.5` / `-lite` (32k context, max 1,000
docs, 600K total-token budget). Jina `jina-reranker-v3` (0.6B, Qwen3-0.6B backbone, 131k context, up
to 64 docs/call, 61.94 BEIR nDCG@10 — https://huggingface.co/jinaai/jina-reranker-v3, arXiv 2509.25085).

### 1.3 ColBERT / late interaction — not applicable to us

I found **no rigorous published ColBERT-vs-cross-encoder latency benchmark**. Vespa's
answerai-colbert-small post (2024-08-14, https://blog.vespa.ai/introducing-answerai-colbert-small/)
gives 33M params, 96-dim embeddings, 32× storage reduction via binarisation, "enables CPU-based
serving" — but **no ms figures**. Claims of "within 2–3% of cross-encoders" circulating in secondary
blogs are not traceable to a primary measurement.

**[I] More importantly, ColBERT is structurally the wrong tool for our problem.** Its speed comes from
*precomputing document token vectors at index time*; it is a retrieval/indexing technique, not a
post-hoc reranker over 50 arbitrary candidates. Vespa's own recommended pattern is ColBERT as an
*intermediate* phase to reduce cross-encoder rerank depth — not to replace the reranker. Adopting it
means rebuilding ingest. **Skip.**

### 1.4 Rerank depth — 50 is fine; keep it

This is the clearest "don't change it" finding. Elastic's **"Exploring depth in a retrieve-and-rerank
pipeline"** (2024-12-05, https://www.elastic.co/search-labs/blog/elastic-semantic-reranker-part-3) is
a BEIR-wide depth sweep to depth 400. I fetched and verified this. **[M]**:

- **72.6%** of dataset/retriever combos show a **Pareto curve** — fast gains, then saturation.
- **20.2% are unimodal** — nDCG **peaks then declines** with more depth (false positives creep in).
  Deeper is genuinely not monotonically better.
- 7.1% degrade vs BM25 outright.
- *"Obtaining 90% of the maximum gain is feasible at a much smaller depth in all scenarios: on average
  we have to re-rank 3× fewer pairs."*

**[M]** Budget table:

| Budget | Avg depth | nDCG increase |
|---|---|---|
| Small | 32 | 41% |
| Medium | 100 | 60% |
| Large | 200 | 66% |

Explicit cost-sensitive recommendation: **rerank the top 30 from BM25**, for ~40% nDCG uplift.

Corroborating evidence on recall lost by reranking fewer candidates — **"Rerank Before You Reason"**
(Sharifymoghaddam & Lin, arXiv:2601.14224, submitted 2026-01-20, rev. 2026-06-01). I verified title,
authors and dates on the arXiv abstract page. The depth ablation (retrieve top-100, rerank at
d ∈ {10, 20, 50}) came from the HTML full text and I could **not** confirm it against the abstract —
**[?]** treat these specific figures as weaker: truncating to **d=20 keeps ~85% of the rerank lift at
5.4× fewer listwise windows; d=10 keeps only 58%**; d=50 with a strong LLM reranker moved NDCG@5 from
19.72 → 46.05. Their headline conclusion **[M, verified in abstract]** is that *"reranking consistently
improves retrieval and end-to-end accuracy, and that moderate reranking often yields larger gains than
increasing search-time reasoning"* — and they found cross-encoders **more cost-effective than listwise
LLM reranking** despite slightly lower peak accuracy.

Vendor guidance: **[M] Anthropic** retrieves **top 150** and reranks to **top 20**; reranked contextual
embeddings + BM25 cut top-20 retrieval failure **5.7% → 1.9% (67% reduction)**. **Pinecone**'s hosted
rerank caps at 100 docs and its tutorials use top_k=25 → top_n=3 — but that page is **undated and cites
2023 material; treat as stale.**

**Conclusion for us:** 50→8 sits between Elastic's "medium budget" and Anthropic's 150→20. **The depth
is not the problem — the reranker model is.** Cutting depth to 20–25 would halve our LLM rerank cost
and give up ~15% of the rerank lift; swapping the reranker gives up ~0% and saves ~90% of the latency.
Do the swap, keep the depth.

### 1.5 The free win to check first

**[I]** Our 8.9s over 5 batches of 10 is ~1.8s per batch, which is exactly the shape of a **sequential**
loop. Sliding-window listwise reranking *is* inherently sequential (each window's output feeds the
next), but if our batches are independent pointwise/listwise scores, parallelising them is a ~5× win
for zero quality change. **Verify whether the batch loop is sequential before doing anything else.**
(See §3.5 for why the observed 1.65× from 4-way threading is probably a routing artefact, not physics.)

---

## 2. Context size

### 2.1 "Lost in the Middle" — real, but not our problem at 8 documents

**Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*, TACL vol. 12 pp. 157–173,
2024** (arXiv 2307.03172, July 2023). https://aclanthology.org/2024.tacl-1.9/

**[M]** U-shaped curve: *"Performance is often highest when relevant information occurs at the
beginning or end of the input context, and significantly degrades when models must access relevant
information in the middle."* For GPT-3.5-Turbo on multi-document QA the first-vs-middle gap is
**~20–23 points** (e.g. 20 docs: 75.8% gold-first vs 53.8% gold-middle — *below* the 56.1% closed-book
score). Doc counts tested: 10, 20, 30.

**⚠️ Confidence note:** two separate fetches of the paper returned different tables for the exact
digits. The **~20-point first-vs-middle gap is robust across both readings**; do not quote the precise
percentages without a direct read of Table 1 / Figure 5.

**[M]** The finding that matters most to us: *"model performance saturates long before retriever
performance saturates."* Going 20 → 50 retrieved documents gained ~1.5% (GPT-3.5) and ~1% (Claude-1.3).
The paper's own conclusion is that **reranking or truncation beats adding documents.**

**Follow-up work confirms rather than refutes it on modern models:**

- **[M] RULER** (Hsieh et al., NVIDIA, arXiv 2404.06654, Aug 2024): of 17 models claiming ≥32k context,
  *"only half of them can maintain satisfactory performance at the length of 32K"* — despite near-perfect
  vanilla needle-in-a-haystack scores. This is the canonical "NIAH is too easy" citation.
- **[M] NoLiMa** (Modarressi et al., Adobe Research, arXiv 2502.05167, Feb 2025, ICML 2025): needles with
  *minimal lexical overlap* with the question. Of 13 models claiming ≥128k, **11 drop below 50% of their
  short-context baseline at 32k**. GPT-4o: 99.3% → **69.7% at 32k**.

**[I] Relevance to Wikify:** at 8 documents and 12k tokens we are nowhere near where these effects bite.
And our queries frequently *do* share literal tokens with the needle (a section number, a rate) — the
easy case where models hold up best. NoLiMa's hard case (paraphrased conceptual query, no lexical
overlap) is the one to worry about, and it's a *retrieval* problem more than a context-length one.

### 2.2 Databricks: quality plateaus long before the context window does

**Blog 2024-08-12** https://www.databricks.com/blog/long-context-rag-performance-llms; **paper** Leng,
Portes, Havens, Zaharia, Carbin, arXiv 2411.03538 (2024-11-05, NeurIPS 2024 workshop).

**[M]** *"Using longer context does not uniformly increase RAG performance."* Retrieval recall keeps
climbing; generation quality plateaus or degrades:

| Model | Peak / decline point |
|---|---|
| Llama-3.1-405B-instruct | declines after **32k** |
| GPT-4-0125-preview | declines after **64k** |
| GPT-4-turbo | peaks at **16k** |
| Claude-3-sonnet | peaks at **16k** |
| DBRX-instruct | best at **8k** |
| Mixtral-8x7b-instruct | optimal at **4k** |

**[M]** Retrieval recall saturation by dataset: Natural Questions 1.0 at **8k**; Databricks DocsQA 0.993
at 96k; HotPotQA 0.890 at 128k; **FinanceBench 0.916 at 128k** (financial documents genuinely need depth).

**[M]** Failure modes catalogued: repeated content, random/irrelevant content, instruction failures
(summarising instead of answering; copyright refusals — Claude-3-Sonnet refused **3.7% at 16k → 49.5% at
64k**; DBRX summarised instead of answering 5.2% → 50.4%), and wrong answers in correct format.

**[I] Conclusion: our 12k is comfortably inside the "everything still works" zone for a frontier model.
Databricks does not support a claim that 12k is hurting our accuracy. Cut context for cost, not quality.**

### 2.3 Parent-document retrieval — the libraries all cap or gate it; we don't

This is the most actionable finding in this section.

- **LangChain `ParentDocumentRetriever`** — two modes: `child_splitter` only (returns the whole parent
  document), or **both `parent_splitter` and `child_splitter`**, where documents are first split into
  *medium-sized* parents. The docs describe mode (b) explicitly as the fix for *"documents [that] are
  too big"*. **The library's own guidance is to cap parent size, not return whole sections.**
  (The canonical how-to URL has been redirected in the docs migration; the commonly-cited 2000-parent /
  400-child example values I could **[?]** not verify in a live fetch.)
- **[M] LlamaIndex `HierarchicalNodeParser` + `AutoMergingRetriever`**
  (https://docs.llamaindex.ai/en/latest/examples/retrievers/auto_merging_retriever/): default chunk
  sizes **2048 / 512 / 128 chars**. Leaf nodes are embedded; parents are pulled from the docstore — and
  **auto-merging replaces children with the parent only when more than *n* of the top-k retrieved chunks
  share that parent.**

**[I] Our 4,524-char average parent is ~1,100 tokens — larger than LlamaIndex's *largest* default level
(2048 chars ≈ 500 tokens) — and we send 8 of them unconditionally. Neither library does this.
Unconditional whole-section expansion on every hit is not the standard pattern; it is the un-tuned
version of the standard pattern.**

### 2.4 Sentence-window retrieval — small real gain, mostly a precision win

**[M] `SentenceWindowNodeParser`** (https://docs.llamaindex.ai/en/stable/api_reference/node_parsers/sentence_window/):
default and near-universal `window_size = 3` sentences either side. Embedding is on the single sentence;
`MetadataReplacementPostProcessor` swaps in the window at synthesis time.

**[M]** LlamaIndex's own eval (https://developers.llamaindex.ai/python/examples/node_postprocessor/metadatareplacementdemo/):

| Retriever | Correctness | Relevancy | Faithfulness | Semantic sim. |
|---|---|---|---|---|
| Sentence Window | **4.37** | **0.93** | 0.93 | 0.96 |
| Base (naive chunking) | 4.22 | 0.90 | 0.93 | 0.96 |

**[I]** +0.15 correctness is small, and it is primarily a *retrieval precision* win (sentence-level
embeddings capture technical terms more sharply), not a context-shrinking technique. Its value to us is
that a ±3-sentence window is far smaller than a whole section while almost always containing the rate —
statutory numbers live in the sentence or its neighbour. Note results vary meaningfully with window size
(run-llama/llama_index#14906); it needs tuning, not the default.

### 2.5 Contextual compression (LangChain) — wrong tool for us

Origin: **LangChain blog, 2023-04-20**,
https://www.langchain.com/blog/improving-document-retrieval-with-contextual-compression. **[M]** Stated
motivation, verbatim: irrelevant text *"1. might distract the LLM from the relevant information 2. takes
up precious space."* The 2023 announcement contains **no quantified compression, cost or latency
numbers** — that is a gap, not a suppressed result. Community measurement **[?]**: ~3× compression
(3,565 → 1,183 chars) in one LangChain OpenTutorial example, methodology unverified.

**[M] LangChain's own docs say plainly:** *"performing additional LLM calls for each retrieved document
is costly and slow"*, and position `EmbeddingsFilter` (no LLM — embed docs + query, drop dissimilar) as
"the cheaper and faster option."

**[I] Verdict: `LLMChainExtractor` is disqualified for us on both axes.** It fires one LLM call *per
retrieved document* — 8 extra calls to save part of 13.7s — and its failure mode is an extractor LLM
dropping the sentence containing the rate. That is precisely the failure we cannot tolerate.
`EmbeddingsFilter` is the variant worth a look: no LLM, drops whole low-similarity hits, ~free.

### 2.6 LLMLingua family — real end-to-end wins, but a bad risk shape for numbers

- **[M] LongLLMLingua** (Jiang et al., arXiv 2310.06839, 2023-10-10 rev. 2024-08-12, **ACL 2024**): on
  ~10k-token prompts — **2×–6× compression**, up to **+21.4% performance with ~4× fewer tokens** on
  GPT-3.5-Turbo (NaturalQuestions), **1.4×–2.6× end-to-end latency speedup**, **94.0% cost reduction**
  on LooGLE.
- **[M] LLMLingua-2** (Pan et al., arXiv 2403.12968, 2024-03-19, **Findings of ACL 2024**): task-agnostic
  compression via token classification with a small encoder (XLM-RoBERTa-large / mBERT) distilled from
  GPT-4. **2×–5× compression**, compressor **3×–6× faster** than prior methods, **1.6×–2.9× end-to-end
  latency speedup**.

**Does the compressor eat the savings? [M] No** — both papers report *end-to-end* speedups, which are by
definition net of compressor time. LLMLingua-2 exists precisely because LLMLingua-1's LLaMA-7B perplexity
scoring was too slow.

**[I] Three reasons to be cautious anyway.** (a) Task-agnostic compression drops tokens by learned
importance, and a bare figure — "18%", "₹2,50,000" — is short, low-perplexity, and structurally the kind
of token that compression discards first. (b) It requires a local model in the request path. (c) Verbatim
citation gets harder: you are citing a compressed prompt, not the source. If pursued, use LongLLMLingua's
**question-aware** variant and gate it behind a regression suite that specifically checks exact-rate
recall. **And see §2.9 — compression saves prefill, and prefill is not where our 13.7s goes.**

### 2.7 Anthropic Contextual Retrieval — the strongest alternative to what we do

**2024-09-19**, https://www.anthropic.com/news/contextual-retrieval. **[M]**, verbatim:

- Contextual Embeddings alone: **top-20 retrieval failure 5.7% → 3.7% (−35%)**
- \+ Contextual BM25: **5.7% → 2.9% (−49%)**
- \+ reranking: **5.7% → 1.9% (−67%)**
- Top-k: they evaluated **5, 10 and 20 chunks and found 20 most performant**.
- Chunk sizing: *"usually no more than a few hundred tokens"*; prepended context **50–100 tokens**/chunk.
- Cost: **$1.02 per million document tokens**, one-time, using prompt caching to avoid re-reading the
  source doc per chunk.

**[I] This is the exact inverse of our strategy, and the better one for our corpus.** Rather than
*enlarging the retrieved unit at query time* to preserve context (paid on every request, in latency and
tokens), it moves the missing context into the *index* as a 50–100-token generated preamble (paid once,
offline, ~$1/M tokens). For ICAI study material where sections repeat structure, a preamble like *"This
chunk is from Chapter 7, Income from House Property, discussing standard deduction"* is exactly what
makes a 300-token chunk self-sufficient — letting us shrink the retrieved unit without losing the context
that made us enlarge it.

### 2.8 Is there a sweet spot for passages-per-answer?

No single canonical number — and the disagreement is informative:

| Source | Recommended k | Basis |
|---|---|---|
| **Anthropic Contextual Retrieval** (2024-09) | **20** | best of {5, 10, 20}, measured |
| **Liu et al., Lost in the Middle** (TACL 2024) | ~**20** | reader saturates before retriever; 20→50 gains ~1–1.5% |
| **Cuconasu et al., The Power of Noise** (SIGIR 2024) | **3–5** | related-but-irrelevant docs actively hurt |

**The Power of Noise: Redefining Retrieval for RAG Systems**, Cuconasu et al., arXiv 2401.14887
(2024-01-26, v4 May 2024, SIGIR 2024). **[M, from abstract — high confidence]:** *"The retriever's
highest-scoring documents that are not directly relevant to the query… negatively impact the
effectiveness of the LLM"*, and counter-intuitively *"adding random documents in the prompt improves the
LLM accuracy by up to 35%."* **[?, from full-text fetch — verify before quoting]:** recommends retrieving
**3–5** documents; place the gold document **near the query** (at the end), not in the middle; Llama2-7B
0.5642 gold-only → 0.4586 with one *related* distractor → 0.5836 with random padding.

**[I] Reconciling 20 vs 3–5:** Anthropic's k=20 is on *small* chunks (few hundred tokens) — 20 × 300 ≈
**6k tokens**. Cuconasu's 3–5 is on Wikipedia-passage-sized units. **The two agree on total context
budget (~4k–8k) and disagree only on granularity.** We are at 8 units × ~1,100 tokens = **12k — more
total context than either recommends, at a granularity coarser than either tested.** The consistent
signal across all three sources: **more, smaller units beats fewer, larger ones.**

I found no Anthropic/OpenAI cookbook, Vespa or Elastic page stating a hard top-k rule; vendor guidance
consistently defers to per-corpus evaluation.

### 2.9 ⚠️ The finding that reframes this whole section: our synthesis is decode-bound

**[I] Arithmetic on our own numbers.** Sonnet 4.6 is $3/$15 per MTok. 12k input = $0.036. Synthesis cost
= 0.92 × $0.047 = $0.0432. Residual $0.0072 ÷ $15/MTok ≈ **~480 output tokens.**

**[M] Artificial Analysis, Claude Sonnet 4.6** (non-reasoning, 10k-input workload, retrieved 2026-08-11,
https://artificialanalysis.ai/models/claude-sonnet-4-6): **TTFT 1.50s, output speed 42.8 tok/s.**

**[I]** 480 ÷ 42.8 = 11.2s decode + 1.5s TTFT = **12.7s — within 7% of our measured 13.7s.** Our
synthesis is **~85–90% decode-bound.**

**Two consequences that invert the intuition behind this research brief:**

1. **Cutting 12k → 4k input is a ~50% COST fix and roughly a 3–4% SPEED fix.** Third-party measurement
   puts 10k-token prefill at ~400–800ms on fast infrastructure **[?]** (https://infercom.ai/blog/llm-inference-speed-explained/
   measures 388ms TTFT at 10k input for gpt-oss-120b). Dropping to 4k saves perhaps 0.4–0.6s.
2. **Prompt caching is a rounding error for us.** Our retrieved chunks change every question, so only
   system + instructions are cacheable. **[I]** If that's ~2k of 12k: 0.9 × 2k × $3/MTok ≈ **$0.0054/question
   (~11% of synthesis cost)**, near-zero latency benefit, and only within the 5-minute TTL.

**⚠️ Action before acting on this: log actual `output_tokens`.** The 480-token figure is inferred from
cost arithmetic, not measured. If it's wrong the whole conclusion shifts. This is a five-minute check
and it should gate every context-reduction decision.

**[M] Prompt caching mechanics, for completeness** (https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching):
5-min cache writes 1.25× base input, 1-hour writes 2×, **cache hits 0.1×**; minimum cacheable prefix is
model-dependent and **not monotonic across generations** — 1,024 tokens on Sonnet 4.6 / Sonnet 5, but
**4,096 on Haiku 4.5**. Below the minimum, caching **silently no-ops**. Cache key is exact bytes from
position 0, render order `tools → system → messages`. Anthropic's launch blog **[V]** claims "up to 90%
cost / up to 85% latency reduction" — but **their own table shows the 85% needs ~100k cached tokens; at
a realistic ~10k prompt the measured latency win is 31%** (1.6s → 1.1s). **[M]** A genuinely underrated
benefit: `cache_read_input_tokens` **do not count toward ITPM rate limits** (except Haiku 3.5), which
raises the effective throughput ceiling.

---

## 3. Pipeline shape and latency

### 3.1 Published latency budgets — retrieval is never the problem

| Source | Number | Type |
|---|---|---|
| **Dropbox Dash** (https://dropbox.tech/machine-learning/building-dash-rag-multi-step-ai-agents-business-users) | **"under 2 seconds for over 95% of our queries"**; sub-100ms for feature retrieval | [M] stated as achieved |
| **Glean Waldo** (2026-04-28, https://www.glean.com/blog/waldo-launch) | **~250ms per LLM call vs ~3s** for GPT-5.4-medium; **~50% lower end-to-end latency, ~25% fewer tokens, no quality regression** | [V] but specific |
| **Exa Fast** (2025-07-29, https://exa.ai/blog/fastest-search-api) | **p50 < 425ms**; notes Google-wrapping APIs have a **~700ms p50 floor**. Exa Instant (Feb 2026): 100–200ms | [V] |
| **Vespa** (https://blog.vespa.ai/eliminating-the-precision-latency-trade-off-in-large-scale-rag/) | *"For an interactive RAG pipeline a defensible production target is roughly 200ms of overall retrieval latency"* | guidance |

**Every serious published retrieval budget is 200ms–2s. Our 12–23ms vector DB is not merely acceptable,
it is excellent. Our 25s is ~100% LLM.**

### 3.2 LinkedIn's honest numbers

**"Musings on Building a Generative AI Product"**, LinkedIn Engineering, Apr 2024,
https://www.linkedin.com/blog/engineering/generative-ai/musings-on-building-a-generative-ai-product

- **[M]** *"For a 200-token reasoning step, even a 10ms TBT increase means an extra 2s of latency."* —
  the cleanest statement of why intermediate LLM hops are latency-toxic.
- **[M]** They reached **80% of the basic experience in the first month**, then spent **four more months**
  chasing 95%.
- **[M]** *"2x/3x the TokensPerSecond"* is achievable **by sacrificing both TTFT and TBT**.
- **[M]** They evaluate up to **500 conversations/day** manually.
- **Note:** they publish TTFT/TBT as *metrics tracked* but **decline to publish absolute SLA numbers**.
  Anyone quoting "LinkedIn's 500ms TTFT target" is inventing it.

### 3.3 Parallelism — OpenAI's own guide names our exact anti-pattern

**OpenAI Latency Optimization guide** (https://developers.openai.com/api/docs/guides/latency-optimization):

> **Make fewer requests** — *"If you have sequential steps for the LLM to perform, instead of firing off
> one request per step consider putting them in a single prompt and getting them all in a single response."*
> **Parallelize** — *"Execute non-sequential steps simultaneously; use speculative execution for dependent steps."*

Their worked example does precisely what we would need: **merge query-contextualisation + retrieval-check
into one prompt** (removing a round trip), then **parallelise the retrieval check with the reasoning step**.
Principle 6 is "Make your users wait less" (streaming, chunking, progress). **Principle 7 is blunt:
"Don't default to an LLM."** **[M]** They also note that shortening JSON field names removed 19 output
tokens, *"potentially saving up to a second"* — output-token count is the dominant latency term, which
matches §2.9.

Other documented parallel architectures:

- **[M] Anthropic, "How we built our multi-agent research system"** (2025-06-13,
  https://www.anthropic.com/engineering/multi-agent-research-system): lead agent spawns **3–5 subagents
  simultaneously**, each using **3+ tools in parallel**; *"these changes cut research time by up to 90%
  for complex queries."* Cost: agents use ~4× the tokens of chat, multi-agent ~15×. Token usage alone
  explains **80% of performance variance**.
- **[M] Azure AI Search agentic retrieval** (https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview,
  updated 2026-07-02) — the most directly relevant vendor design: query planning uses an LLM only at
  `low`/`medium` reasoning effort; **at `minimal` the LLM planning step is skipped entirely** and queries
  go straight to knowledge sources. All subqueries run **simultaneously**, each semantically reranked.
  **Microsoft shipped a "skip the router LLM" tier because the hop is expensive.**
- **[M] DoorDash** (https://careersatdoordash.com/blog/doordash-llms-to-build-content-embeddings-for-search-and-recommendations/):
  the stated principle is that **the LLM never sits in the request path** — LLMs build content embeddings
  *offline*; serving is ANN retrieval fused with structured taxonomy retrieval.
- **[M] Elastic** (2026-06-15, https://www.elastic.co/search-labs/blog/llm-query-routing-elastic-workflows)
  *does* use an LLM router — but engineered around the latency: the router sees **only search-result
  metadata** (scores, categories, complexity labels), never document bodies, keeping the prompt to "a few
  hundred tokens," explicitly because *"a slow router makes every answer slow."*

**[M] ChatGPT search** (https://openai.com/index/introducing-chatgpt-search/, 2024-10-31) is a fine-tuned
GPT-4o post-trained on synthetic data distilled from o1-preview; it *"rewrites your query into one or more
targeted queries"* then issues **additional follow-up queries after reviewing initial results.** OpenAI
publishes **no latency budget** for it.

### 3.4 Streaming and perceived latency — and a result that argues against panic

- **[M] Nielsen, "Response Times: The 3 Important Limits"** (1993, canonical,
  https://www.nngroup.com/articles/response-times-3-important-limits/): 0.1s direct manipulation; **1.0s
  uninterrupted flow of thought**; **10s limit of attention** — beyond that users task-switch and need a
  completion estimate.
- **[M] Nielsen, "Think-Time UX"** (2025-10-23, https://www.uxtigers.com/post/think-time-ux) revises this
  for AI with a five-phase "Cognitive Latency Stack": Perception 0–400ms, Comprehension 0.4–2s, Decision
  2–10s, **Execution 10–60s+**, Recovery minutes-to-hours. Tasks over 10s **must support backgrounding**.
  He argues AI's **latency variance** is what breaks the old guidance.
- **[M] CHI 2026, "The Impact of Response Latency and Task Type on Human-LLM Interaction and Perception"**
  (https://arxiv.org/html/2604.06183v1) **[?] — I did not independently verify this preprint; treat with
  care.** 240 participants, TTFT manipulated at 2s / 9s / 20s with streaming throughput held constant:
  responses at 2s were rated **less thoughtful** than at 9s or 20s (2s M=5.76 vs 9s M=6.09, p=.008);
  **usefulness peaked at 9s** (M=6.44 vs 6.19 at 2s, p=.021); trust, clarity, relevance and NASA-TLX
  workload showed **no significant latency effect**; latency was consciously detected by 26.6% at 2s,
  66.3% at 9s, **81.5% at 20s**.
- **[M] Buell & Norton, "The Labor Illusion"**, *Management Science* 57(9), 2011
  (https://pubsonline.informs.org/doi/10.1287/mnsc.1110.1376): with operational transparency, people
  **prefer longer waits to instant identical results.**

**[I] Read:** for an evidence-gathering task like cited QA, a **9–15s visibly-working** wait is plausibly
near-optimal for perceived quality — but our 25s is past the point where 81% consciously notice, and
crucially the CHI study manipulated *time to first token with streaming behind it*. **This is not a
licence to keep a 25s blank screen; it is an argument that streaming + visible progress buys us more
than shaving the last few seconds.**

**Be careful:** widely-repeated "300ms TTFT is where users stop noticing / 800ms raises abandonment"
figures circulate on SEO-grade blogs with **no primary study behind them. Do not cite those.** The
defensible anchors are Nielsen and Buell & Norton.

### 3.5 Why 4-way threading gave us only 1.65× — mostly expected, partly fixable

Four stacked causes:

**(a) Continuous batching merges our requests. [M]** NVIDIA NIM benchmarking docs
(https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html): *"As the number of concurrent requests
increases, total system TPS increases while TPS per user decreases as latency increases… beyond
[saturation] TPS can decrease."* Our 4 requests don't get 4 GPUs; they join a shared, memory-bandwidth-bound
decode batch alongside other tenants.

**(b) The curve is sublinear even on dedicated hardware. [M]** Databricks (2023-10-12,
https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices), MPT-7B on 1×A100:
batch 1 → 0.9 req/s; **batch 4 → 3.2 req/s (3.5×)**; batch 64 → 12.5 (14×). **Only 3.5× at 4-way on a
dedicated, empty GPU.** **[M]** Anyscale (2023-06-22): at QPS=1 continuous batching improves latency at
all percentiles; **at QPS=4 it degrades.**

**(c) OpenRouter's default routing disperses our threads — this is the fixable part. [M]**
https://openrouter.ai/docs/features/provider-routing: default routing is **price-weighted with
inverse-square weighting**. Our 4 parallel requests are **independently sampled from a weighted provider
distribution**; some land on a fast provider, some slow, and **wall-clock is set by the slowest.**
Fix: `sort: "throughput"` (or `:nitro`), or an explicit `order` array with `allow_fallbacks: false`.
Also available: `preferred_max_latency` / `preferred_min_throughput` over rolling 5-min p50/p75/p90/p99
windows — but these **deprioritise, they do not exclude.**

**(d) Bursts trip acceleration limits. [M]** Anthropic's rate limits use a **token bucket with continuous
replenishment**, warning *"60 RPM might be enforced as 1 request per second"*; they also document
**acceleration limits** triggered by sharp usage increases. Neither Anthropic nor OpenAI documents a
per-key *concurrency* cap — the limits are rate-shaped, not concurrency-shaped. The widely-repeated
"~50 concurrent requests" OpenRouter figure appears only in third-party guides; **not documented.**

**[I] Verdict: re-run the 4-way test pinned to a single provider before concluding anything.** A
meaningful chunk of the missing 2.35× is probably routing dispersion, not physics. Then sweep 1/2/4/8/16
to find our own knee.

**Two gotchas that bite parallel fan-out specifically. [M]** Anthropic: *"Cache entries only become
available after the first response begins. For parallel requests to hit the same cache, wait for the first
response before sending subsequent requests."* A 4-way fan-out over a shared prefix produces **4 cache
writes at 1.25×, not 1 write + 3 reads** — 5× the input cost of the serial-warm case, zero latency
benefit. **Warm the prefix with one serial call first** (`max_tokens: 0` pre-warms, write charge only).
Cloudflare AI Gateway carries the same warning.

### 3.6 Caching — prefix yes, semantic no

**Prefix/KV caching (contractual, reliable):** see §2.9 for Anthropic. **[M] OpenAI**
(https://developers.openai.com/api/docs/guides/prompt-caching): automatic, **1,024-token prefix minimum**,
writes 1.25× on GPT-5.6+, **30-minute minimum retention**. Critically: *"Requests are routed to a machine
based on a hash of the initial prefix"* — and they recommend keeping a given `prompt_cache_key` to **~15
req/min**, past which they spill to more machines and **hit rate degrades**. **[M] Gemini**
(https://ai.google.dev/gemini-api/docs/caching): implicit caching on by default for 2.5+; cached input at
**10% of standard price**; minimum 4,096 tokens (Gemini 3.5 Flash) or 2,048 (2.5 Flash); explicit caches
carry **storage charges of $4.50/M tok/hour (Pro) or $1.00/M tok/hour (Flash)** — at low request rates an
explicit cache can cost more than it saves.

**Semantic caching — weak, and wrong for us:**

- **[M] "Category-Aware Semantic Caching for Heterogeneous LLM Workloads"** (arXiv 2510.26835, 2025-10-29,
  IBM Research / Tencent / Red Hat), measured on production at ~100K queries/hour: **head categories hit
  40–60%** (code gen 55%, API docs 45%); **tail categories hit 5–15%** (medical 6%, legal 10%) and are
  30–40% of traffic. **False-positive rate 15% at threshold 0.80, 3% at 0.90.** Failure example:
  `sort_ascending` matching `sort_descending`. Remote vector-DB lookup ~30ms; in-process HNSW 2ms.
- **[M] vCache** (arXiv 2502.03771, ICLR 2026, Berkeley/TUM incl. Zaharia & Gonzalez): static similarity
  thresholds — what GPTCache, Portkey and every commercial gateway ship — *"do not give formal correctness
  guarantees, result in unexpected error rates."* Their learned per-prompt threshold gives **up to 26×
  lower error at matched latency** and 12.5× more cache hits.
- **[V] GPTCache** claims 61.6–68.8% hit rate — on *curated benchmark datasets*. **Portkey's own figure
  for RAG use cases is ~20%.** Helicone and Cloudflare AI Gateway are **exact-match only**.

**[I] Semantic caching is disqualified for cited QA on two independent grounds:** measured RAG hit rates
of 15–25%, and ICLR-2026 evidence that default static thresholds serve wrong answers at unbounded rates —
which in a *cited* product means a confidently wrong statutory rate with a real-looking citation attached.
**Exact-match caching of identical queries is fine and free.**

---

## 4. Cheap alternatives to the LLM router

### 4.1 The ladder, cheapest first

| Option | Measured latency | Source |
|---|---|---|
| TF-IDF + linear SVM | sub-ms **[I]** | RAGRouter-Bench (below) |
| Embedding router, **local** encoder (MiniLM/SetFit) | single-digit ms CPU | OATS |
| DistilBERT classifier | **p50 9ms, p99 <50ms** (CPU) | Switchcraft / HF Infinity |
| Small local LLM router (1.5B) | **42ms** | Tiny-Critic RAG |
| LLM API router | **785ms** (gpt-4o-mini) / **our 2.1s** | Tiny-Critic RAG |

### 4.2 The most on-point evidence

**⚠️ Confidence note:** the three most directly relevant papers below are **2026 arXiv preprints that I
did not independently verify** — **[?]**. Their *direction* is corroborated by the older, peer-reviewed
work in §4.3–4.4; treat the precise figures as provisional.

- **[?] RAGRouter-Bench** (arXiv 2604.03455, 2026-04-07) — 7,727 queries, 4 domains, 3 query types
  (factual / reasoning / summarization). Best config **TF-IDF + SVM: macro-F1 0.928, accuracy 93.2%**,
  simulating **28.1% token savings**. Striking detail: **lexical TF-IDF beat MiniLM sentence embeddings
  by 3.1 macro-F1 points** — *"surface keyword patterns are strong predictors of query-type complexity."*
- **[?] Tiny-Critic RAG** (arXiv 2603.00846, 2026-03-01) — the cleanest analogue of our exact swap:

  | | routing overhead | routing F1 | cost / 10k queries |
  |---|---|---|---|
  | Heavy-CRAG (GPT-4o-mini API router) | **785ms** | 0.934 | $3.00 |
  | Tiny-Critic (Qwen-1.7B, LoRA r=16, constrained decoding) | **42ms** | 0.912 | $0.06 |

  **94.6% latency reduction and 98% cost reduction for 2.2 points of routing F1.**

**[I] Our exhaustive-vs-semantic decision is very likely a lexical-signal problem** — quoted strings,
section numbers, "list all…" → exhaustive; conceptual phrasing → semantic. A TF-IDF+SVM trained on a few
hundred labelled queries is the highest-expected-value experiment in this document: **2.1s → sub-millisecond.**

### 4.3 Adaptive-RAG — a weak router still beats no router

**[M] Adaptive-RAG** (arXiv 2403.14403, **NAACL 2024**), FLAN-T5-XL (3B):

| Strategy | steps/query | s/query | avg F1 |
|---|---|---|---|
| Single-step | 1.00 | 1.00 | 44.31 |
| Multi-step | 4.69 | 8.81 | 48.85 |
| **Adaptive-RAG** | **2.17** | **3.60** | **46.94** |
| Oracle router | 1.28 | 2.11 | 56.28 |

Two findings that matter most:

- **The classifier is only 54.52% accurate overall** — and routing *still* wins on the efficiency/accuracy
  frontier. **A weak router beats no router.**
- **Classifier size barely matters:** T5-Small (60M) 45.83 F1 → Base (223M) 45.97 → Large (770M) 46.94.
  **~1 F1 point for a 13× parameter increase.** This is direct, peer-reviewed evidence against spending
  2.1s of frontier-LLM time on the decision.

### 4.4 Small classifiers vs LLMs, generally

- **[M] SetFit** (arXiv 2209.11055, Sep 2022): with **8 labelled examples per class**, comparable to T-Few
  3B while **27× smaller**; on RAFT, SetFit-RoBERTa-Large (355M) scores **71.3% vs GPT-3 (175B) zero-shot
  at 62.7%**. Trains in **30 seconds / $0.025** on a V100.
- **[M] Bucher & Martini** (arXiv 2406.08660, Jun 2024): fine-tuned small models beat zero-shot
  GPT-3.5/GPT-4/Claude Opus **in all cases** across four classification tasks.
- **[M] arXiv 2411.05050**: BERT fine-tuned on **200 samples = 71.1%**, already above **GPT zero-shot at
  70.2%**; GPT few-shot only catches BERT at ~1,000 samples.
- **[?] Switchcraft** (arXiv 2605.07112, May 2026, Microsoft): DistilBERT router adds **3–17ms**, 722
  queries/s peak; **82.9% accuracy, 84% inference cost reduction.**

### 4.5 RouteLLM and the honest planning number

**[M] RouteLLM** (Ong et al., arXiv 2406.18665, ICLR 2025; https://lmsys.org/blog/2024-07-01-routellm):
at **95% of GPT-4 quality retained** — **>85% cost reduction on MT-Bench, 45% on MMLU, 35% on GSM8K.**
Best router = **matrix factorization** (95% of GPT-4 quality with only **26% of calls to GPT-4**, 14%
with LLM-judge augmentation). Beat commercial routers Martian and Unify AI with **>40% additional
savings**; transferred to a new model pair without retraining.

**Two caveats.** (a) The paper reports **cost, not routing latency**; of their four router types only the
BERT-classifier and matrix-factorization variants are in our latency class — **the causal-LLM-classifier
router is itself an LLM call and would not help us.** (b) **Note the spread: the easier the benchmark, the
bigger the saving.** ICAI statutory QA is far closer to MMLU than to MT-Bench — **45% is our honest
planning number, not 85%.**

### 4.6 semantic-router (Aurelio AI) — verified, with two warnings

- **[V] The "4ms vs 53,860ms" claim on https://www.aurelio.ai/semantic-router is methodologically broken**
  — their own footnote reveals the "Claude Opus" number is a 3.86s *time-to-first-token* figure inflated
  to 53,860ms. **No independent latency benchmark of the library exists.**
- **[M] The local-vs-API encoder split is the real story.** all-MiniLM-L6-v2 (22M) is single-digit-to-low-tens
  of ms on CPU. But Milvus benchmarked 20+ embedding APIs (2025-05-23,
  https://milvus.io/blog/we-benchmarked-20-embedding-apis-with-milvus-7-insights-that-will-surprise-you.md)
  and found API embedding calls take *"hundreds of milliseconds to several seconds"*, with **3–4× inflation**
  calling NA endpoints from Asia — directly relevant to us. A self-hosted bge-base on **4 CPU cores matched
  the fastest API provider. Use a local encoder or you give back most of the win.**
- **⚠️ Security [?]:** versions 0.1.8–0.1.14 are reported yanked on PyPI for CVE-2026-42208 (unbounded
  `litellm` pin resolving to a compromised wheel). **Pin >=0.1.15** if adopted. I did not verify this on
  PyPI directly.

### 4.7 The option nobody in our team has considered: delete the router

**[?]** Hybrid BM25 + dense with Reciprocal Rank Fusion is repeatedly claimed to beat either mode alone
(e.g. 91% recall@10 vs 65–78%), with the argument that *users send both query types in the same session,
so there is no reliable way to classify which approach each query needs ahead of time* — and RRF fusion
costs ~6ms. **The specific numbers come from marketing blogs and are unverified. But the architectural
argument is the strongest case against our router existing at all**, and it is the pattern Dropbox Dash
and DoorDash both describe. Running both retrieval modes and fusing removes 2.1s *and* removes a
correctness failure mode (router picks wrong mode → wrong evidence → wrong rate).

---

## 5. Model choice for synthesis

### 5.1 Groundedness and parametric recall are different capabilities — and only one collapses when you shrink

This is the central finding, and the measurement is clean.

**[M] FACTS Benchmark Suite**, Google DeepMind, blog 2025-12-09, paper arXiv 2512.10791:

| Model | Overall | **Grounding** | **Parametric** |
|---|---|---|---|
| Gemini 3 Pro | 68.8 | 69.0 | 76.4 |
| Gemini 2.5 Pro | 62.1 | **74.2** | 63.2 |
| GPT-5 | 61.8 | 69.6 | 55.8 |
| **Gemini 2.5 Flash** | 50.4 | **69.9** | **30.7** |
| GPT-5 mini | 45.9 | 58.3 | 16.0 |
| GPT o3 | 52.0 | **36.2** | 57.1 |

**Gemini 2.5 Flash beats Gemini 3 Pro and GPT-5 on Grounding while scoring less than half of Gemini 2.5
Pro on Parametric.** The capability that collapses when you shrink is *recall from weights*; the
capability that survives is *using facts placed in front of it*. And o3 — a large reasoning model —
scores 36.2 on grounding: **reasoning and groundedness are distinct and can be anti-correlated.** All 15
models scored below 70% overall; nobody has solved this.

Same pattern a generation earlier — **[M] FACTS Grounding v1** (arXiv 2501.03200, 2025-01-06): Gemini 1.5
**Flash 82.9%** vs Gemini 1.5 **Pro 80.0%**, Claude 3.5 Sonnet 79.4%, GPT-4o 78.8%.

**[M] Vectara HHEM leaderboard** (https://github.com/vectara/hallucination-leaderboard, README last
updated **2026-05-11**, judge HHEM-2.3, 7,700+ articles, temp 0, 118 models). Hallucination rate, lower
better:

- **OpenAI:** gpt-5.4-nano **3.1%** < gpt-5.4-mini 5.5% < gpt-5.4 **7.0%** < gpt-5.4-pro 8.3%.
- **Google:** gemini-2.5-flash-lite **3.3%** < gemini-2.5-pro 7.0% < gemini-2.5-flash 7.8%.
- **Anthropic:** claude-haiku-4-5 **9.8%** — **best in the Claude family**, beating sonnet-4-6 (10.6%)
  and opus-4-5 (10.9%).
- **Qwen3:** 4B 5.7% / 8B 4.8% / 14B 5.4% / 32B 5.9% — **flat across an 8× parameter range**.
- Counterexamples: Phi-4-mini 23.5%, ministral-3-3b 24.2%. **"Small" is not automatically safe.**

**[M] Caveat, quoted from the README:** it measures *summarization* consistency, not QA, and the authors
concede *"an extractive summarizer model that just copies and pastes… score[s] 100%."* **[I]** Part of
the small-model advantage is stylistic — terse copy-heavy outputs have less surface to be unfaithful on.
For our use case that bias is *aligned* with what we want, but the board overstates gap-closure generally.

### 5.2 The countervailing evidence — where small models genuinely fail

- **[M] "Sufficient Context: A New Lens on RAG"** (Joren et al., Google, arXiv 2411.06037, **ICLR 2025**)
  — the most important negative finding: strong models do well with sufficient context but *"often output
  incorrect answers instead of abstaining when the context is not"* sufficient — while **small models
  (Mistral 3, Gemma 2) hallucinate or abstain even when context IS sufficient.** Their selective-generation
  method improves correct-among-answered by **2–10%**.
- **[?] "Can Small Language Models Use What They Retrieve?"** (arXiv 2603.11513, 2026-03-12): oracle
  retrieval, EM% on questions unanswerable from weights — SmolLM2-360M **0.0**, Qwen2.5-1.5B 10.0, 3B 12.8,
  7B 14.6; 61–100% of oracle failures are "irrelevant generation"; concludes robust context use *"may
  require models larger than 7B."* **Single-author unreviewed preprint, 4-bit quantized, strict EM, and
  absolute numbers far below what published RAG work reports with gold passages. Direction only.**
- **[M] TAT-LLM** (arXiv 2401.13223) error analysis: **48% of total errors are "wrong evidence"** — grabbing
  the wrong cell/number, not doing the wrong arithmetic. **And it degrades badly when the figure lives in a
  table rather than a sentence.** ICAI study material is full of rate tables. This is our real risk.
- **[?] TaxPraBen** (arXiv 2604.08948, 2026-04-22), 19 LLMs / 7,300 instances of Chinese tax practice:
  closed-source large models top-3 consistently; **open-source 7B models significantly underperform**.
  **Important scope note: no RAG condition — this is closed-book**, measuring parametric tax knowledge,
  the axis where small models are known to lose. **It does not refute the grounded-extraction case.**

**The line to hold:** *copying a rate out of a cited span* (extraction — small models near parity) is a
different task from *computing/comparing/deriving a rate* (derivation — even frontier models fail at rates
we cannot ship). Route derivation to a deterministic calculator or a large model.

### 5.3 Current pricing and speed (verified from official pages, 2026-08-11)

**[M] Anthropic** (https://platform.claude.com/docs/en/about-claude/pricing):

| Model | API ID | In $/MTok | Out $/MTok | Cache read | AA (non-reasoning, 10k in) |
|---|---|---|---|---|---|
| Claude Opus 5 | `claude-opus-5` | 5 | 25 | 0.50 | — |
| **Claude Sonnet 5** | `claude-sonnet-5` | **2** *(intro → 3 on 2026-09-01)* | **10** *(→ 15)* | 0.20 | **TTFT 1.91s, 62.6 tok/s** |
| Claude Sonnet 4.6 *(ours)* | `claude-sonnet-4-6` | 3 | 15 | 0.30 | **TTFT 1.50s, 42.8 tok/s** |
| **Claude Haiku 4.5** | `claude-haiku-4-5-20251001` | **1** | **5** | 0.10 | **TTFT 0.97s, 88.7 tok/s** |

Haiku 4.5: 200k context (not 1M), 64k max output, **min cacheable prefix 4,096 tokens**, no adaptive
thinking. Anthropic's own reduce-latency page names Haiku 4.5 as the speed pick.

**[M] Google** (https://ai.google.dev/gemini-api/docs/pricing, page updated 2026-08-11):
`gemini-3.6-flash` $1.50/$7.50 (235.4 tok/s); `gemini-3.5-flash-lite` $0.30/$2.50 (385.7 tok/s);
`gemini-2.5-flash` $0.30/$2.50; `gemini-2.5-flash-lite` $0.10/$0.40 (**fastest TTFT on the AA
leaderboard: 0.31s**).

**⚠️ [M] Artificial Analysis caveat:** for reasoning models AA's "time to first token" measures the
**first reasoning token**, assuming 2,000 reasoning tokens where counts aren't available. That is why
Gemini 3.5 Flash-Lite shows 11.14s TTFT — that is a thinking-on configuration, not what we'd run.

**[M] Provider spread on identical weights** — Sonnet 4.6, 10k input, 2026-08-11
(https://artificialanalysis.ai/models/claude-sonnet-4-6/providers):

| Provider | TTFT | Output tok/s |
|---|---|---|
| Google Vertex | **1.14s** | 44.2 |
| Amazon Bedrock | 1.33s | **46.0** |
| Anthropic direct | 1.50s | 42.8 |
| Azure/Foundry | 1.93s | 42.6 |

Blended price identical ($2.31/1M) across all four. **[I] ~1s off our wall clock from a routing change
alone, at zero accuracy risk.**

### 5.4 ⚠️ The documented quick win Anthropic explicitly warns about

**[M]** https://platform.claude.com/docs/en/build-with-claude/effort, verbatim: *"Sonnet 4.6 defaults to
`high` effort. **Explicitly set effort when using Sonnet 4.6 to avoid unexpected latency.**"* Recommended
default for 4.6 is **`medium`**; **`low`** is recommended for *"high-volume or latency-sensitive
workloads… chat and non-coding use cases."* It lives in `output_config.effort`, no beta header, and
scales *all* output tokens.

**[I]** At ~85–90% decode-bound (§2.9), a 30–40% output-token cut is worth roughly **3–4 seconds**.
Caveat: changing `effort` between requests invalidates prompt caching (it renders into the prompt).

**[M] Thinking check:** on **Sonnet 4.6, thinking is OFF** until you set `thinking: {type: "adaptive"}` —
so we are probably clean. But on **Opus 5 / Sonnet 5 / Fable 5, thinking is ON by default with
`display: "omitted"`** — invisible, billed as output, counts toward `max_tokens`. **This is the trap if
we upgrade to Sonnet 5.** For scale: **[M]** AA measures Claude Sonnet 5 with adaptive reasoning at max
effort at **TTFT 182.29s**, vs 1.91s non-reasoning. Same model, two orders of magnitude.

### 5.5 Making a fast model safe for exact figures

**Anthropic Citations API** (https://platform.claude.com/docs/en/build-with-claude/citations, launched
2025-01-23) is the only mechanism here with a real guarantee. **[M]**, verbatim: *"Because the API parses
citations into the response formats described in the following sections and extracts `cited_text`
directly, citations are **guaranteed to contain valid pointers to the provided documents**."* The API
resolves the pointer and slices text out of our document **server-side** — `cited_text` is a byte-for-byte
slice of our input, not model-generated prose.

**What it does NOT guarantee: that the claim in the accompanying text is entailed by the cited span.
Claude can attach a valid citation to a sentence whose number it got wrong.** (Formalised in **[M]**
"Correctness is not Faithfulness in RAG Attributions", arXiv 2412.18004.) **Citations gives auditability,
not correctness.**

Mechanics: plain text → sentence-level `char_location`; PDF → `page_location`; `search_result` blocks →
`search_result_location`. **For numeric span verification we want plain-text-per-chunk (char offsets).**
Streaming works (`citations_delta`). Cost: document text billed as normal input; **`cited_text` does NOT
count toward output tokens** — so quote-heavy answers are *cheaper* via Citations than via prompt-based
"quote your source." No latency figures published.

**Three hard constraints that shape architecture. [M]** (a) **Citations + Structured Outputs returns a
400** — incompatible "because citations require interleaving citation blocks with text output." We cannot
have guaranteed-real spans and a guaranteed JSON schema in one call; do free-text cited generation first,
extract structure second. (b) **Text only — no image citations.** Scanned PDFs without an extractable text
layer silently fail to be citable. (c) **[?] Whether OpenRouter passes Citations through is undocumented
and unconfirmed. Test this before it locks in our architecture** — if it doesn't, that decides
direct-vs-gateway for us.

**[V]** Anthropic's marketing claims for Citations — "up to 15% recall accuracy increase", customer Endex
"source hallucinations from 10% to 0%" — have **no published methodology. Testimonials, not evidence.**

**The competition:**

- **[M] Vertex AI Check Grounding**
  (https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding) — a standalone verifier
  and the most useful Google piece here. Input: answer ≤4,096 tokens + ≤200 facts ≤10,000 chars each.
  Output: **support score 0–1**, cited chunks, **sentence-level claims** with citation indices and
  per-claim support scores, and a `groundingCheckRequired` flag so boilerplate isn't penalised.
  **Stated latency target under 500ms.** Semantics: *"Perfect grounding requires that every claim… be
  wholly entailed by the facts"* and **partially-true claims count as ungrounded** — exactly the right
  strictness for "12% vs 12.5% GST."
- **[M] OpenAI file search** `file_citation` annotations give `file_id`, `filename`, and an `index` that
  is a position **in the output text, not a span in the source. No span guarantee** — materially weaker.
  **And a `source_quote` field in a Structured Outputs schema is model-generated text: a hallucination
  surface, not a safeguard. This is the trap to avoid.**

**Prompting techniques with measured numbers:**

- **[M] Chain-of-Verification (CoVe)** (Meta AI, arXiv 2309.11495, Findings of ACL 2024): **FACTSCORE
  55.9 → 71.4 (+28% relative)**. **The critical mechanism: verification questions are answered
  *independently, without conditioning on the draft*** — that is what breaks the self-confirmation loop.
- **[M] "Attribute First, then Generate"** (ACL 2024, aclanthology.org/2024.acl-long.182): select content
  → plan → generate, so the selected source segments *are* the attribution. More concise citations,
  quality maintained, **significantly reduced human verification time.**
- **[M] Anthropic's reduce-hallucinations guidance**: allow "I don't have enough information"; for >20k-token
  documents, **extract word-for-word quotes first, then answer from only those quotes**; post-hoc, *"for
  each claim find a direct quote… if you can't find a supporting quote, remove that claim."*

**Constrained decoding — the literature genuinely splits.** Against: **[M]** "Let Me Speak Freely?"
(EMNLP 2024 Industry) — significant reasoning decline under format restriction. For: **[M]**
JSONSchemaBench (arXiv 2501.10868, Jan 2025), 10K real schemas / 6 frameworks — *"constrained decoding
consistently improves… downstream tasks up to 4%."* **[I] Net read: neutral-to-slightly-positive on
frontier models, plausibly negative on small ones, and dominated by whether the model gets to think in
free text before the constrained emission.** Latency overhead itself is negligible **[M]** (XGrammar
<40µs/token, default in vLLM/SGLang/TensorRT-LLM as of March 2026) — but **first-request-per-schema
grammar compilation adds real latency, so keep schemas stable.**

### 5.6 ⚠️ Cheap NLI verifiers exist, but cannot be trusted as gates

| Verifier | Size | Accuracy | Cost/latency |
|---|---|---|---|
| **HHEM-2.1-Open** (Vectara) | 110M | ~71.8–76.6% | **<600MB RAM fp32; ~1.5s for 2k tokens on x86 CPU** — no GPU |
| **AlignScore-large** | 355M | matches/beats GPT-4-based metrics | GPU-cheap |
| **MiniCheck-FT5** | 770M | GPT-4-level on LLM-AggreFact | **400× lower cost than GPT-4** |
| **Bespoke-MiniCheck-7B** | 7B | **77.4%** avg, SOTA on LLM-AggreFact | ~200ms/GPU |

**[M] Tamber et al., EMNLP 2025 Industry** (aclanthology.org/2025.emnlp-industry.54): zero-shot LLM judges
stay *"below 78% balanced accuracy and F1-macro below 72%"*; on adversarial FaithBench the best is
**68.8%**; prior work found LLM classifiers near **50% — "negligible ability to identify hallucinated
responses."** **~77% balanced accuracy means roughly 1 in 4 unsupported claims slips through.**

**[M] FaithJudge** (same paper) is the honest fix: put nine human-annotated peer responses to the *same
source* in the judge prompt → **84.0% acc / 82.1 F1 on FaithBench**. Judge sensitivity is bought with
annotated examples, not with a bigger judge — but that costs per-source human annotation.

**[M] On Ragas:** it self-reports 0.95 human agreement on faithfulness (authors' own WikiEval). But FACTS
v1 measured **+3.23% self-preference bias**, and metric-human correlations are **inflated when the model
pool spans very strong and very weak systems.** **[I] A Ragas faithfulness of 0.95 has a per-item error
rate plausibly in the 20–30% range against human labels. It cannot certify a tax rate. Use it as a
regression signal across releases, not a release gate.**

### 5.7 Cascades — and the honest number

- **[M] FrugalGPT** (arXiv 2305.05176, TMLR): matches GPT-4 with **up to 98% cost reduction**. **⚠️
  Per-dataset best case on 2023 benchmarks with a GPT-3.5→GPT-4 price gap that no longer exists. Do not
  budget against 98%.**
- **[M] "Cost-Saving LLM Cascades with Early Abstention"** (arXiv 2502.09054, 2025-02-13): letting the
  *small* model abstain early rather than deferring everything gives, averaged across
  GSM8K/MedMCQA/MMLU/TriviaQA/TruthfulQA/XSum: **−13.0% cost, −5.0% error rate, −2.2% test loss, +4.1%
  abstention.** Explicitly framed for **risk-sensitive domains (finance, medicine)** — our case. Small
  effects, right shape.
- **Products:** OpenRouter's Auto Router is powered by Not Diamond, `cost_quality_tradeoff` 0–10, no
  surcharge. **I found no independent measured benchmark for any commercial router** — every circulating
  number traces back to RouteLLM/FrugalGPT. 2025–26 blog posts claiming "40–60% cost-per-task reduction"
  are content marketing with no methodology. **Do not cite them.**

### 5.8 OpenRouter overhead

**[M] Opper.ai, "LLM Router Latency Benchmark 2026"** (2026-04-21,
https://opper.ai/blog/llm-router-latency-benchmark-2026), 200 calls/provider on GPT-4.1, 95% CIs:

| | TTFT | tok/s |
|---|---|---|
| OpenAI direct | 0.712s | 81.8 |
| **OpenRouter** | **0.640s** | **73.2** |

OpenRouter was **70ms faster to first token but ~10% slower on throughput**. **[I]** For a decode-bound
workload that throughput haircut is ~1.1s of our 13.7s — *if* it generalises from OpenAI to Anthropic
backends, which is unproven. The authors warn results are non-transferable across region/config/time-of-day.

**[V]** OpenRouter's own Latency and Performance doc contains **no ms figures**. It does note cold edge
caches add latency in a new region for 1–2 min, and that low credit balances trigger aggressive cache
expiry (keep ≥$10–20).

---

## 6. What we are missing

Specific, and ordered by how badly a naive engineer would miss it.

1. **We are optimising the wrong half of synthesis.** §2.9: ~85–90% of the 13.7s is *decode*, not prefill.
   Cutting the 12k-token prompt is a **cost** fix. The **speed** fix is fewer output tokens (`effort`,
   brevity prompting) and a faster-decoding model. **Log `output_tokens` today** — the whole conclusion
   rests on an inferred 480.

2. **Streaming.** We have none. **[I]** It doesn't reduce generation time; it moves user-visible start
   from 25s to ~1.5s. Combined with §3.4, this is likely worth more perceived-quality points than any
   two other items here. Compatible with citation verification: stream prose, hold citation markers back,
   verify async, patch markers in. **[M]** ZeroEntropy report post-hoc span-pointer extraction at
   ~sub-100ms per claim, ~400ms for a 4-sentence answer, parallelisable. **[M]** CiteFix (arXiv 2504.15629)
   is the academic version — citation correction as post-processing.

3. **A deterministic numeric verifier — the only non-probabilistic control available to us.**
   **[M] Proof-Carrying Numbers (PCN)**, Solatorio (World Bank), arXiv 2509.06902, **2025-09-08** — opens
   with literally our risk ("reporting 6.0% instead of 5.7%"). Design: numbers emitted as **claim-bound
   tokens** tied to a structured record; a **verifier in the renderer/presentation layer — deliberately
   not in the model, so the model cannot spoof a checkmark** — checks each number against a declared policy
   (exact equality / rounding / aliases / tolerance-with-qualifier); **fail-closed by default**, only
   claim-verified numbers get a verification mark. Formal proofs of soundness, completeness under honest
   tokens, fail-closed behaviour. **⚠️ Protocol paper with formal guarantees and no empirical benchmark**
   — but the architecture transplants directly.
   **[M]** Supporting: "Real-Time Detection of Hallucinated Entities in Long-Form Generation" (arXiv
   2509.03531) — **entities (dates, numbers, names, citations) have clean token boundaries and can be
   verified token-by-token as they stream**, whereas free-form claims need post-hoc extraction that
   destroys token alignment. This is what makes streaming compatible with verification.
   **The gap:** I found **no published paper measuring numeric-error reduction from string-matching every
   number in an answer against retrieved context.** It is well-known folklore, formalised only by PCN,
   never benchmarked. **[I] It is nonetheless the highest-value control on this list: deterministic, O(1)
   cost, 100% recall on verbatim mismatch.**

4. **Tables are our real failure mode, not free-form hallucination.** **[M]** TAT-LLM: **48% of errors are
   wrong-evidence selection**, and degradation is worst when the figure lives in a table. **[M]** FACTS
   Suite parametric axis (Flash 30.7 vs Pro 63.2) says the other failure is **silently answering from stale
   weights.** Both are pipeline problems; **neither is fixed by model choice.** An ICAI rate table chunked
   badly is a correctness bug that no reranker or model upgrade will find.

5. **Batch APIs: confirmed useless for interactive, legitimate for eval. [M]** Anthropic Message Batches:
   50% discount, "most batches finish in less than 1 hour," results at completion **or 24 hours, whichever
   first**. Same shape at OpenAI and Google. **But use them for offline eval runs and for pre-generating a
   known FAQ head** — for an exam-revision product, the head of the query distribution is probably fat.

6. **Speculative decoding is not a knob we control. [M]** Providers including Anthropic run draft-model
   speculation at the infrastructure level, transparent to API callers. OpenAI's Predicted Outputs is the
   only exposed variant and only helps with a near-correct draft — irrelevant for open-ended synthesis.

7. **Anthropic Fast mode exists but not for our tier — track it. [M]**
   https://platform.claude.com/docs/en/build-with-claude/fast-mode: `speed: "fast"`, **up to 2.5× output
   tok/s, same weights, same quality** — exactly what a decode-bound pipeline wants. **Opus 5 / Opus 4.8
   only**, research preview, Claude API only, $10/$50 per MTok, explicitly "focused on OTPS, not TTFT."
   Not on any Sonnet yet.

8. **`max_tokens` is a cap, not a reservation** — no direct latency cost. Real output-length control is
   brevity prompting plus `effort`.

---

## 7. Ranked recommendations

Scored **L** = latency saved, **C** = cost saved, **R** = risk to answer correctness.
**⚠️ = could produce a wrong statutory rate — disqualifying unless mitigated.**

| # | Action | L | C | R | Notes |
|---|---|---|---|---|---|
| **1** | **Log `output_tokens` and set `output_config.effort` explicitly (`medium`, then trial `low`)** | **~3–4s** | ~15% | **None→Low** | Anthropic's docs literally warn about this for Sonnet 4.6. Free. But `low` effort on a *derivation* question could shorten reasoning — gate behind an eval. |
| **2** | **Replace the LLM reranker with a dedicated cross-encoder** (Cohere Rerank 4 Fast / Voyage rerank-2.5-lite / Jina v3.5; or self-hosted MiniLM-class on CPU) | **~8s** | small | **None→Improves** | Best-evidenced item here. Voyage [M]: dedicated rerankers *beat* LLMs by 12–15% NDCG@10. Oracle [M]: 96×512tok = 0.54s. **Caveat: our 1,100-token chunks put us in the 0.5–1.0s band, not 150ms — measure.** |
| **3** | **Turn on streaming** | **~24s perceived** | 0 | **None** | Doesn't reduce compute; moves visible start from 25s to ~1.5s. Nielsen + CHI 2026 + Buell&Norton all point the same way. Stream prose, patch citation markers in after async verification. |
| **4** | **Replace the LLM intent router with TF-IDF+SVM, or delete it and fuse both modes with RRF** | **~2.1s** | ~5% | **None→Improves** | Adaptive-RAG [M]: a 60M router loses ~1 F1 vs 770M; a *weak* router still beats no router. Fusing removes a failure mode (wrong mode → wrong evidence → ⚠️ wrong rate). Prefer fusion if recall allows. |
| **5** | **Build the deterministic numeric verifier (PCN shape)** | 0 (+~50ms) | 0 | **Large reduction** | Regex every rate/threshold/date out of the answer; string-match against `cited_text` spans; explicit rounding/alias policy; **fail-closed**. **The only deterministic control in this document.** Worth more than any model choice. |
| **6** | **Pin the provider / route for throughput** (`sort: "throughput"`, or go direct to Vertex/Bedrock) | **~1s**, plus possibly much of the missing 2.35× on parallel calls | 0 | **None** | [M] Vertex 1.14s TTFT vs Anthropic-direct 1.50s at identical price. [M] OpenRouter defaults to **price-weighted** routing — we are currently optimised for price without meaning to be. |
| **7** | **Gate parent-section expansion** (LlamaIndex auto-merge: promote to parent only when ≥2 of top-k children share it; otherwise send the child + Anthropic-style contextual preamble) | ~0.5s | **~40–50%** | **Low ⚠️** | Nobody recommends unconditional whole-section expansion. **But this is the item that can drop the sentence containing the rate.** Requires a regression suite checking exact-rate recall before it ships. |
| **8** | **Adopt Anthropic Contextual Retrieval at index time** ($1.02/M doc tokens, one-off) | indirect | indirect | **Reduces ⚠️** | [M] 49% retrieval-failure reduction, 67% with reranking. Makes #7 safe by making small chunks self-sufficient. **Do #8 before #7.** Our corpus is ~1,150 chunks — this is a cheap one-time job. |
| **9** | **Reduce rerank depth 50 → 25** | ~0 (after #2) | small | **Low ⚠️** | [M] Elastic: depth 32 gives 41% of nDCG uplift; [?] d=20 keeps ~85% of the rerank lift. **Only worth doing if we stay on an LLM reranker.** After #2 the latency argument evaporates — keep 50. |
| **10** | **Prompt-cache the system prompt** | ~0 | ~11% of synthesis | **None** | [M] Min prefix 1,024 tok on Sonnet 4.6. Retrieved chunks are volatile and can never be cached. **Budget zero latency benefit.** Also: warm serially before any fan-out or all calls pay a write. |
| **11** | **Evaluate Sonnet 5 at `medium` effort** (intro pricing ends 2026-09-01) | ~3s | **~33%** | **Low** | [M] $2/$10 intro vs $3/$15; 62.6 vs 42.8 tok/s. **⚠️ Trap: thinking is ON by default on Sonnet 5 with `display: "omitted"` — set `thinking: {type: "disabled"}` or latency explodes.** |
| **12** | **Evaluate Haiku 4.5 for synthesis, with a hard numeric gate + abstention→escalate** | **~7s** | **~67%** | **Medium ⚠️** | [M] $1/$5, 88.7 tok/s, TTFT 0.97s, **9.8% HHEM — best in the Claude family**. [M] FACTS Suite says groundedness survives shrinking; parametric recall does not. **Only safe behind #5 and a table-heavy eval set.** Watch: 4,096-token min cache prefix. |
| **13** | **Exact-match query caching** | up to 25s on hits | up to 100% on hits | **None** | Free. Exam-revision traffic likely has a fat head. |
| **14** | **Add a `groundingCheckRequired`-style verifier pass** (Vertex Check Grounding, or MiniCheck-FT5 self-hosted) | **+0.5s** | small increase | **Reduces, but weakly** | [M] Vertex targets <500ms and treats partially-true claims as ungrounded — right strictness. **But [M] EMNLP 2025: ~77% balanced accuracy means ~1 in 4 unsupported claims slips through. A signal, not a gate.** |
| — | **Semantic caching** | — | — | **⚠️ Disqualified** | [M] 15–25% RAG hit rates; [M] ICLR 2026 vCache: static thresholds have unbounded error. A near-miss cache hit in a cited product = confidently wrong rate with a real-looking citation. |
| — | **`LLMChainExtractor` contextual compression** | **negative** | negative | **⚠️ Disqualified** | 8 extra LLM calls; failure mode is dropping the sentence with the number. LangChain's own docs call it "costly and slow." (`EmbeddingsFilter` is fine.) |
| — | **LLMLingua prompt compression** | ~0.4s | ~50% | **⚠️ High** | [M] Real end-to-end wins (1.4–2.9×) — **but those are prefill wins and we are decode-bound (§2.9), so it buys us almost nothing on latency.** And bare figures are exactly the low-perplexity tokens compression discards. **Cost-only benefit at high correctness risk. Skip.** |
| — | **ColBERT / late interaction** | — | — | **N/A** | Wrong tool: an indexing technique, not a post-hoc reranker over 50 candidates. Requires rebuilding ingest. |

### Suggested sequencing

**Week 1 (free, no correctness risk, ~15s off the clock):** #1, #3, #6, #13 — plus the two measurements
that gate everything else: log `output_tokens`, and re-run the 4-way rerank threading test pinned to one
provider.

**Week 2 (~10s off the clock, still low risk):** #2 (cross-encoder rerank), #4 (kill the LLM router),
#10.

**Week 3+ (needs an eval set first):** #5 (numeric verifier), then #8 → #7, then #11/#12 behind #5.

**Do not start #7, #11, or #12 without an exam-question regression suite that specifically checks exact
statutory-rate recall, weighted toward figures that live in tables** — per TAT-LLM, wrong-evidence
selection from tables is 48% of errors and it is our single most likely path to a wrong rate.

**Realistic destination: ~25s → ~6–8s wall clock, ~1.5s to first token, at roughly a third of current
cost, with better numeric safety than today.** The bulk of that comes from items #1–#4, none of which
touch the correctness-critical path.

---

## 8. Confidence register — what I verified myself vs what I did not

**Verified by direct fetch during this research:**
- Voyage "The Case Against LLMs as Rerankers" — all NDCG, speed, cost and setup figures.
- Elastic "Exploring depth in a retrieve-and-rerank pipeline" — pattern distribution, budget table,
  90%-of-gain claim, top-30 recommendation, 2024-12-05 date.
- arXiv 2601.14224 "Rerank Before You Reason" — title, authors (Sharifymoghaddam & Lin), dates, abstract.

**Well-sourced but not personally re-verified** (fetched by research agents from primary URLs, dates and
figures internally consistent): Anthropic Contextual Retrieval, Citations, prompt caching, effort and
pricing docs; Databricks long-context study; Liu et al. TACL 2024; RULER; NoLiMa; LlamaIndex and LangChain
docs; LLMLingua papers; Adaptive-RAG; RouteLLM; SetFit; FACTS Grounding v1 and FACTS Benchmark Suite;
Vectara HHEM leaderboard; OpenAI latency and caching guides; OpenRouter provider-routing docs; Oracle
Cohere benchmark; Jina/Elastic reranker figures; Nielsen; Buell & Norton.

**Weak — cited but flagged [?], do not build on without re-checking:**
- The 2026 arXiv preprints: RAGRouter-Bench (2604.03455), Tiny-Critic RAG (2603.00846), CHI 2026 latency
  study (2604.06183), Switchcraft (2605.07112), "Can Small Language Models Use What They Retrieve?"
  (2603.11513), TaxPraBen (2604.08948). Their *direction* is corroborated by older peer-reviewed work;
  the precise figures are not confirmed.
- The d=10/20/50 rerank-depth ablation figures from arXiv 2601.14224 (from HTML full text, not the abstract).
- The Cuconasu "3–5 documents" recommendation and Llama2-7B figures (full-text fetch, not abstract).
- Exact percentages in Liu et al. (two fetches disagreed; the ~20-point gap is robust, the digits are not).
- LangChain `ParentDocumentRetriever` example chunk sizes (docs URL redirected).
- Cohere rerank-3.5 deprecation date; semantic-router CVE-2026-42208.
- Whether OpenRouter passes through the Anthropic Citations API — **undocumented, and it is a genuine
  architectural fork. Test it.**

**Marketing claims cited only as marketing [V]:** Jina's 150ms figure, Cohere's "optimized for low
latency", Glean Waldo's 50%/25% numbers, Exa's p50s, Anthropic's Citations recall/hallucination
testimonials, GPTCache hit rates, aurelio.ai's semantic-router benchmark (which is demonstrably
methodologically broken).

**Explicitly rejected as unsourced:** "300ms TTFT is where users stop noticing"; "800ms raises
abandonment"; "LinkedIn's 500ms TTFT target"; "LLaMA2-7B + retrieval beats LLaMA2-70B closed-book";
OpenRouter's "~50 concurrent request" cap; all 2025–26 blog claims of "40–60% cost-per-task reduction"
from commercial routers.
