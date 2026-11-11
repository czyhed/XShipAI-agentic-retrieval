# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python FastAPI service with a Vite and vanilla TypeScript verification workbench. The browser uses the generated TypeScript SDK and contains no retrieval logic.

## Users

Engineers and evaluators validating retrieval pipelines, answer evidence, citations, latency, and offline quality against a configured formal retrieval source.

## Product Purpose

AgenticRetrival is a reusable retrieval and answer service. It lets users run and compare registered retrieval profiles, inspect the evidence sent to an answer model, verify citations, and run deterministic and optional model-based evaluations.

## Positioning

The public API, candidate model, pipeline profiles, observability, evaluation, and workbench are source-neutral. Each deployment connects to one controlled, read-only retrieval source through a Python adapter. The current formal adapter reads Chunk Lab PostgreSQL/pgvector data.

## Operating Context

Users open AgenticRetrival directly or from a plain Chunk Lab link. Both paths open the same global retrieval workbench. Chunk Lab does not create a task or send records, vectors, filters, secrets, model settings, or return URLs.

## Capabilities and Constraints

- Global retrieval only in V1.
- Registered profiles: Dense-only, BM25-only, Hybrid, and Hybrid+Rerank.
- One configured read-only data source per deployment; no source selector or knowledge-base administration.
- Python FastAPI is the only retrieval implementation.
- Answer generation uses only the server-built Context Pack.
- Langfuse is optional external observability; Ragas is explicit offline evaluation.
- Missing or failed external services return explicit states and never fabricate output.
- Profile comparisons live only in the current browser session.
- Desktop web is the product target; narrow desktop layouts must remain usable.

## Evidence on Hand

- Formal PostgreSQL table: `rag.vector_records_v2_d1024`.
- Existing FastAPI service, OpenAPI generator, generated TypeScript SDK, and verification UI.
- PRD, specification, and implementation tickets under `docs/` and `.scratch/`.

## Product Principles

- Make the actual retrieval path inspectable.
- Keep evidence and citations server-authoritative.
- Prefer explicit degraded, skipped, abstained, and failed states over fallback behavior.
- Keep the public contract generic while adapters own source-specific storage details.
- Optimize the workbench for repeated engineering verification, not presentation.

