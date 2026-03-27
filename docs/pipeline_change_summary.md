# Pipeline Change Summary

This document summarizes the backend pipeline changes made in the recent fix passes, along with the key implementation decisions that were taken.

## Purpose

The work focused on stabilizing the Self-RAG backend pipeline in three areas:

- restoring broken embedding and vector-index contracts
- fixing graph and retrieval control-flow bugs
- improving relevance ranking, citation semantics, and evaluation quality

This note is intended as a project-facing explanation and implementation record, not a full API reference.

## 1. Embeddings and Vector Index Contracts

### What changed

- Restored missing embedding helpers in `backend/app/services/ingestion/embeddings.py`:
  - `build_embedding_provider_from_values(...)`
  - `validate_embedding_dimensions(...)`
  - `OllamaEmbeddingProvider`
- Kept `build_embedding_provider(settings)` as the top-level entrypoint for existing ingestion paths.
- Added formal vector search support in `backend/app/services/ingestion/vector_index.py`:
  - `VectorSearchMatch`
  - `VectorIndex.query(...)`
  - in-memory query implementation
  - Pinecone query implementation
- Hardened Pinecone index targeting so `PINECONE_HOST` is preferred over `PINECONE_INDEX_NAME`.

### Decisions

- `ollama` is the active embedding provider and should support explicit dimensions.
- Pinecone host-based targeting is the preferred production path.
- Query results should request metadata and avoid unnecessary values when retrieval only needs metadata.

### Why

- The pipeline could not import cleanly because the embedding router depended on helpers that did not exist.
- Retrieval code already assumed a vector-search contract that the vector index layer did not implement.
- Existing tests already described the intended behavior, so the implementation was aligned to those expectations.

## 2. Graph Import Safety and Retrieval Control Flow

### What changed

- Made `backend/app/pipeline/__init__.py` lazy so importing `app.router.embedding_router` no longer triggers `graph -> retrieval -> embedding_router` during package initialization.
- Restored real retrieval decisioning in `backend/app/pipeline/nodes/retrieval_decision.py`:
  - no selected bins still force parametric mode
  - selected bins now execute the configured `retrieval_decision` prompt/provider
  - `retrieve` and `skip` are both live branches again
- Fixed empty-retrieval rewrite flow in `backend/app/pipeline/nodes/retrieval.py`:
  - empty retrieval no longer flips the run to parametric immediately
  - rewritten queries can retry actual vector search
  - final fallback to parametric happens only when the answer path has no grounded docs

### Decisions

- Selected bins should still allow the LLM classifier to choose `skip`.
- Empty retrieval should remain a grounded-retry state until rewrite attempts are exhausted.
- The package-level `app.pipeline` API should remain available without eager graph imports.

### Why

- Direct imports of `embedding_router` could fail with a circular-import error.
- The retrieval-decision node had dead code after an unconditional early return.
- The rewrite loop could never recover documents after the first empty retrieval because later retrieval calls short-circuited.

## 3. Relevance Ranking, Citation Scores, and Retrieval Hardening

### What changed

- Updated `backend/app/pipeline/nodes/relevance_grader.py` to:
  - grade documents concurrently with `asyncio.gather`
  - keep per-document grader scores in `relevance_scores`
  - sort `relevant_documents` by grader score descending
  - route to rewrite or generate using average graded score instead of binary pass ratio
- Updated `backend/app/pipeline/nodes/answer_generator.py` so:
  - grounded context follows the re-ranked document order
  - `Citation.score` comes from relevance-grader confidence, not vector similarity
- Hardened `backend/app/pipeline/nodes/retrieval.py` so:
  - malformed `item_id` metadata does not abort retrieval
  - duplicate selected-bin namespaces do not silently overwrite each other in memory

### Decisions

- Relevance-grader confidence is the authoritative ranking signal for grounded answers.
- `Citation.score` should mean grader confidence for grounded citations.
- `pipeline.relevance_threshold` is now treated as an average-score threshold.
- Namespace collision handling is worth defending even though persisted bins already have a database uniqueness constraint.

### Why

- Final ordering ignored the relevance-grader scores even though those scores were already computed.
- Citation scores exposed vector similarity, which no longer matched the final ranking logic.
- Binary pass ratio could route to rewrite or generate in ways that did not reflect actual score quality.
- Retrieval could fail entirely on one malformed metadata record.

## 4. Evaluation Harness Improvements

### What changed

- Reworked `backend/app/pipeline/evals/harness.py` so evaluation metrics are based on citation overlap rather than retrieval-mode equality:
  - `retrieval_hit_rate`: recall-like overlap against expected citation chunk IDs
  - `relevance_precision`: precision over cited chunk IDs
  - `grounding_score`: F1-style overlap score over expected vs actual citation chunk IDs
- Kept retrieval mode as a required correctness check, but stopped using it as a stand-in for ranking and grounding quality.

### Decisions

- Evaluation should fail when citation quality regresses even if retrieval mode is still correct.
- The existing dataset shape is sufficient for meaningful offline scoring as long as citation overlap is used properly.

### Why

- The old harness could pass while ranking or grounding quality regressed, because it reduced multiple metrics to mode equality.

## 5. Test Coverage Added or Updated

### Targeted regression coverage

- import-cycle regression for `app.router.embedding_router`
- retrieval-decision `skip` branch with provider trace and prompt version capture
- empty retrieval followed by successful rewritten retrieval
- empty retrieval exhausting rewrite attempts and falling back to parametric answer generation
- relevance-based reranking and citation score semantics
- average-score routing behavior
- concurrent relevance grading behavior
- malformed `item_id` metadata handling
- duplicate selected-bin namespace handling
- eval harness failure when grounding quality regresses but retrieval mode stays correct

### Commands run successfully

- `python3 -m pytest backend/tests/test_phase6_ollama_embeddings.py -q`
- `python3 -m pytest backend/tests/test_phase6_vector_index.py -q`
- `python3 -m pytest backend/tests/test_phase4_pipeline_imports.py -q`
- `python3 -m pytest backend/tests/test_phase4_rag_pipeline.py -q`
- `python3 -m pytest backend/tests/test_phase4_evals.py -q`

## 6. Notes and Non-Goals

- This summary covers the changes made in the recent agent-driven fix passes only.
- Some unrelated local modifications were already present in the worktree and were intentionally left alone.
- A broader backend test run previously surfaced an unrelated import issue around `recover_ingestion_worker`; that problem was outside the scope of these pipeline fixes.

## 7. Files Changed in These Passes

Primary implementation areas:

- `backend/app/services/ingestion/embeddings.py`
- `backend/app/services/ingestion/vector_index.py`
- `backend/app/pipeline/__init__.py`
- `backend/app/pipeline/nodes/retrieval_decision.py`
- `backend/app/pipeline/nodes/retrieval.py`
- `backend/app/pipeline/nodes/relevance_grader.py`
- `backend/app/pipeline/nodes/answer_generator.py`
- `backend/app/pipeline/evals/harness.py`

Primary regression coverage:

- `backend/tests/test_phase6_ollama_embeddings.py`
- `backend/tests/test_phase6_vector_index.py`
- `backend/tests/test_phase4_pipeline_imports.py`
- `backend/tests/test_phase4_rag_pipeline.py`
- `backend/tests/test_phase4_evals.py`
