# Domain Context

## Retrieval Source

The single controlled, read-only corpus configured for one AgenticRetrival deployment. The current formal Retrieval Source is Chunk Lab PostgreSQL/pgvector.

## Retrieval Profile

A registered combination of retrieval stages and defaults. V1 profiles are Dense-only, BM25-only, Hybrid, and Hybrid+Rerank.

## Retrieval Run

One execution of a query through a Retrieval Profile. It owns its rewritten queries, candidates, scores, statuses, timings, and version metadata.

## Answer Run

A separate execution that performs retrieval, builds a Context Pack, invokes the Answer Generator, validates citations, and returns an answer status. It never reuses browser-held candidates as authoritative evidence.

## Context Pack

The exact ordered evidence entries sent by the Python service to the Answer Generator.

## Citation

A server-validated reference to one Context Pack entry, enriched from the Retrieval Source with document version, source block IDs, 1-based page, and quoted text.

## Profile Comparison

A browser-session view comparing Retrieval Runs across profiles. It is not persisted and does not automatically generate multiple answers.

## Evaluation Run

An asynchronous offline evaluation of a server-registered dataset. Deterministic metrics may complete before optional Ragas metrics.

## Chunk Lab Link

A plain new-tab link to the AgenticRetrival UI. It carries no validation task, records, vectors, filters, secrets, model configuration, or return URL.

