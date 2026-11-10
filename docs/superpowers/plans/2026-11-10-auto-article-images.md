# AI 成文自动配图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off AI Assistant switch that submits cover and inline image prompts from a completed article to the existing asynchronous image-generation flow.

**Architecture:** Keep orchestration in `StudioView.vue` to minimize backend changes. After the article SSE ends successfully, parse the JSON article contract, create one image job per cover/inline prompt through the existing API, and reuse the current job polling/result display. Bind the launch to the current content item and isolate individual failures.

**Tech Stack:** Vue 3, existing API client, SSE generation client, FastAPI image-generation endpoint, existing job polling.

---

### Task 1: Add automatic image-generation state and controls

**Files:**
- Modify: `apps/web/src/views/StudioView.vue`

- [ ] Add `aiAutoImages = ref(false)` beside the AI assistant state.
- [ ] Add a switch control in the AI panel labeled “自动生成配图”, with text explaining that cover and inline images start asynchronously after article generation.
- [ ] Keep the switch default-off and disable it while AI generation is running.

### Task 2: Parse article prompts and submit asynchronous image jobs

**Files:**
- Modify: `apps/web/src/views/StudioView.vue`

- [ ] Add a local parser that accepts raw streamed output, strips an optional Markdown code fence, extracts the outer JSON object, and returns `coverPrompt` plus non-empty `inlinePrompts`.
- [ ] Add `startAutoImageGeneration(rawOutput, contentItemId)` that only runs for `article` tasks, checks the active content item, submits one `cover` job and one `inline` job per prompt via `/ai/image-generations`, and inserts each returned job into `imgJobs` with a label and prompt.
- [ ] Reuse `startImgPoll()` after jobs are submitted; retain successful results and let failed jobs remain independently visible.
- [ ] When output cannot be parsed or contains no prompts, show a non-blocking notice and leave the article saved.
- [ ] Invoke the function from `runAi`’s `onEnd` after refreshing the article, only when the switch was enabled and the generation was not interrupted or errored.
- [ ] Capture the content item ID at generation start so switching drafts cannot attach jobs to another item.

### Task 3: Expose retry/status affordances for automatic jobs

**Files:**
- Modify: `apps/web/src/views/StudioView.vue`

- [ ] Extend failed-job rendering to show the prompt label and a “重试” button.
- [ ] Add `retryImageJob(job)` that resubmits the same prompt and replaces the failed job with a new queued job while preserving other jobs/results.
- [ ] Show queued/running automatic jobs in the existing progress area and preserve existing manual generation behavior.

### Task 4: Validate the feature

**Files:**
- Test: existing frontend build and available API tests

- [ ] Run the frontend production build from `apps/web`.
- [ ] Run the backend test suite from `apps/api` if dependencies are available.
- [ ] Verify default-off behavior, automatic cover/inline job submission, isolated failure/retry behavior, and unchanged manual image generation behavior.
