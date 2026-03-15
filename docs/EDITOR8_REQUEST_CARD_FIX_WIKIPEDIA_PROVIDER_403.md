# Request Card: Fix Wikipedia Provider 403 Errors Thoroughly

## Summary

`editor8` is sending some search queries to the Wikipedia provider that result in:

```text
403 Client Error: Forbidden for url: https://vi.wikipedia.org/w/api.php?action=opensearch&search=...
```

This is not a missing API key problem.

Wikipedia public read APIs normally do **not** require an API key.
The current failure is caused by a combination of:

- wrong query shape for Wikipedia
- missing/ineffective request headers on the actual `requests.get(...)` call
- inappropriate language selection for the query
- lack of provider-specific routing rules

This card requests a complete fix so the Wikipedia provider becomes robust, low-noise, and operationally predictable.

---

## Problem Statement

The current search system fans out the same query to all text-search providers, including Wikipedia.

That causes Wikipedia to receive web-search-style queries such as:

- `site:deloitte.com ...`
- long quoted title strings
- domain filters
- keyword-stuffed commercial/company research phrases

Those queries are poor fits for Wikipedia search and can trigger 403s or low-value requests.

At the same time, the current implementation creates a `wikipediaapi.Wikipedia(...)` object with a user-agent, but the actual `requests.get(...)` call to `w/api.php` does not reuse that user-agent explicitly.

So the provider is both:

- semantically querying Wikipedia in the wrong situations
- and doing so with an incomplete HTTP request setup

---

## Current Likely Root Causes

### 1. Query routing is too naive

The same raw query is sent to all providers.

That is wrong for Wikipedia because Wikipedia is not a general web search engine.
Queries containing patterns like these should not be sent to it as-is:

- `site:`
- domain names
- heavy quoted fragments
- search-operator syntax
- company/site-specific research prompts

### 2. User-Agent is not applied to the actual HTTP request

The provider constructs:

- `wikipediaapi.Wikipedia(language=..., user_agent=...)`

but then performs a separate:

- `requests.get("https://<lang>.wikipedia.org/w/api.php", ...)`

without explicitly setting the same user-agent header.

That means the custom user-agent shown in logs is not guaranteed to be the user-agent used by the request that received 403.

### 3. Default language is too rigid

The provider defaults to:

- `lang="vi"`

But the failing query is clearly English-language and domain-specific.

Sending English keyword-heavy research queries to `vi.wikipedia.org` is low quality even when it does not hard-fail.

### 4. No provider-specific query normalization

Wikipedia should receive an encyclopedic entity/topic query, not a raw web-search query.

The system currently lacks:

- provider-specific query cleaning
- provider skip rules
- provider fallback language strategy

### 5. Poor failure classification

Wikipedia 403s are currently logged as provider errors, but not clearly classified as:

- request-shape issue
- upstream rejection
- temporary availability issue
- likely-non-retryable provider misuse

---

## Non-Goal

Do not “fix” this by:

- adding a Wikipedia API key requirement
- silently suppressing all Wikipedia errors without improving routing
- retrying 403s aggressively

Wikipedia public search should remain keyless unless product requirements explicitly change.

---

## Required Fixes

## 1. Fix HTTP request construction in the Wikipedia provider

### File

- `editor8/backend/src/editor8/tools/providers/wikipedia.py`

### Required changes

Ensure the actual `requests.get(...)` call includes a proper explicit `User-Agent` header.

Also consider adding:

- `Accept: application/json`
- a small provider timeout policy

The same user-agent identity should be used consistently for:

- `wikipediaapi.Wikipedia(...)`
- raw `requests.get(...)`

### Expected outcome

If Wikipedia rejects the request, it should not be because the raw request was missing the intended user-agent.

---

## 2. Stop sending obviously non-Wikipedia queries to Wikipedia

### Files

- `editor8/backend/src/editor8/tools/base.py`
- optionally provider-specific helper module(s)
- optionally `editor8/backend/src/editor8/tools/providers/wikipedia.py`

### Required changes

Introduce provider-routing rules so the Wikipedia provider is skipped for web-search-shaped queries.

At minimum, skip Wikipedia when the query contains signals like:

- `site:`
- raw domain names such as `deloitte.com`
- multiple quoted fragments
- clearly commercial/site-scoped research syntax

This routing can be implemented either:

- centrally in `TextSearchService`
- or inside `WikipediaProvider.search()`

Preferred direction:

- keep provider-specific logic in the provider or a provider-routing helper

### Expected outcome

Wikipedia should only be queried for entity/topic lookups that are plausibly encyclopedic.

---

## 3. Add provider-specific query normalization for Wikipedia

### Required changes

When Wikipedia **is** used, normalize the query before sending it to `opensearch`.

Examples of normalization:

- strip `site:...`
- strip raw domain filters
- collapse repeated quoted fragments
- extract core entity/topic tokens
- reduce keyword stuffing

For example, instead of sending a giant raw research query, Wikipedia should receive something closer to:

- company name
- person name
- article/report title
- broad concept phrase

### Expected outcome

Wikipedia receives short, topic-oriented queries it can realistically answer.

---

## 4. Add language strategy instead of hard-wiring `vi`

### Files

- `editor8/backend/src/editor8/tools/providers/wikipedia.py`
- possibly config if policy needs configurability

### Required changes

Implement a language-selection strategy for Wikipedia queries.

Recommended behavior:

- use configured default language for Vietnamese/native queries
- use `en` when the query is clearly English-heavy
- optionally attempt fallback from `vi` to `en` if the initial query is not suitable or returns no useful results

This should be explicit and deterministic, not accidental.

### Expected outcome

An English business/technology query should not be forced into `vi.wikipedia.org` by default.

---

## 5. Handle 403 as a non-fatal provider degradation

### Required changes

A Wikipedia 403 must not materially degrade the whole text-search pipeline.

The provider should:

- log clearly
- return `[]`
- classify the failure reason
- avoid noisy retries

If the provider already returns `[]`, keep that behavior, but improve:

- error classification
- log message quality
- routing so the error happens much less often

### Expected outcome

Wikipedia failure becomes an isolated provider issue, not an operational mystery.

---

## 6. Improve logging for diagnosis

### File

- `editor8/backend/src/editor8/tools/providers/wikipedia.py`

### Required additions

Logs should clearly state:

- original query
- normalized query
- selected language
- whether the provider was skipped
- why it was skipped
- whether fallback language was attempted
- HTTP status code on failure
- whether failure is likely request-shape-related

Add stable events such as:

- `wikipedia.search.skipped`
- `wikipedia.search.request`
- `wikipedia.search.fallback`
- `wikipedia.search.error`

### Expected outcome

Operators can tell whether Wikipedia failed because:

- query was unsuitable
- request was rejected
- fallback was attempted
- provider was intentionally bypassed

---

## 7. Add test coverage

### Files

- add tests under `editor8/backend/tests/`

### Required test cases

#### Case A: encyclopedic Vietnamese query

- provider uses configured/default language
- query is sent normally

#### Case B: English entity query

- provider selects `en` or uses fallback strategy correctly

#### Case C: query contains `site:`

- Wikipedia is skipped
- or query is normalized before request, depending on chosen policy

#### Case D: domain-specific long research query

- provider does not send raw query directly to Wikipedia

#### Case E: HTTP 403 from Wikipedia

- provider returns `[]`
- error is logged clearly
- no crash propagates upward

#### Case F: short-result enrichment path

- snippet enrichment via `wiki.page(title)` still works after the refactor

### Expected outcome

This class of provider misuse should not regress silently.

---

## 8. Document provider responsibility clearly

### Required documentation updates

Document that Wikipedia is for:

- factual encyclopedic summaries
- people
- places
- organizations
- concepts
- notable named entities

Document that Wikipedia is **not** for:

- domain-filtered web search
- site-specific content retrieval
- commercial website search
- operator-heavy search syntax

This should be reflected in developer docs and provider comments.

---

## Recommended Implementation Order

1. add explicit headers to the real HTTP request
2. add Wikipedia skip rules for clearly unsuitable queries
3. add query normalization
4. add language selection/fallback logic
5. improve logs
6. add tests
7. update docs

This order reduces production noise first, then improves quality and maintainability.

---

## Acceptance Criteria

This fix is complete only when all of the following are true:

- Wikipedia public usage still requires no API key
- the raw HTTP request uses the intended explicit user-agent
- Wikipedia no longer receives obviously web-search-only queries unchanged
- English-heavy queries are not blindly forced to `vi.wikipedia.org`
- a 403 from Wikipedia does not create confusion about auth requirements
- provider logs clearly explain whether the request was sent, skipped, normalized, or failed
- automated tests cover routing, normalization, language behavior, and 403 handling

---

## Success Metric

After this fix:

- Wikipedia 403s should become rare
- when they do happen, operators should immediately know they are not API-key problems
- the Wikipedia provider should contribute useful results when appropriate and stay out of the way when not appropriate

The real success condition is not “fewer stack traces”.
It is:

- correct provider usage
- explicit routing semantics
- predictable operational behavior
