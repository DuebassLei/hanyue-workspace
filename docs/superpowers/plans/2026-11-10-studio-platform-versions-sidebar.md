# Studio Platform Versions Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display the platform versions section in the left sidebar directly below Recent Versions.

**Architecture:** Reuse the existing platform-version markup, collapse state, and data in `StudioView.vue`. Move the section into the sidebar after Recent Versions, then remove editor-specific spacing styles and apply sidebar spacing.

**Tech Stack:** Vue 3 SFC, scoped CSS, Vite.

---

### Task 1: Relocate platform versions

**Files:**
- Modify: `apps/web/src/views/StudioView.vue`

- [ ] Move the existing `studio-doc-variants` section from the editor header area to immediately after the `studio-versions` section in the left sidebar.
- [ ] Preserve the existing `variantsCollapsed` toggle, list contents, and `v-if="detail.variants.length"` condition.
- [ ] Remove editor-specific horizontal margins from `.studio-doc-variants` and retain compact bordered styling appropriate to the sidebar; update the mobile override consistently.
- [ ] Run `npm run build` from `apps/web` and confirm it succeeds.
