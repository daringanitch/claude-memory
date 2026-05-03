# Design Spec: Claude Memory UI

**Date:** 2026-05-03
**Status:** Approved
**Source:** Design handoff `clearspeed-memory.zip` (at `~/claude-memory/design_handoff_claude_memory/`)

---

## Overview

A single-page editorial UI for browsing, searching, and managing the claude-memory vector database. No build step — a single `ui.html` file served directly from the existing MCP server at `GET /ui`. The backend gains ~10 REST endpoints added to `mcp-server/server.py` using the Starlette integration already present.

Visual design: deep indigo (`#312888`) + cyan (`#009CFF`) + Mulish (sans) + Cardo (serif italic accent). Editorial feel: 2px indigo rules, tabular numerals, generous whitespace, sentence-case copy. Fully specified in the handoff README.

---

## Architecture

### Files changed / added

| Path | Change |
|---|---|
| `mcp-server/server.py` | Add 10 REST routes + `GET /ui` using existing Starlette routing |
| `mcp-server/ui.html` | **New** — single-file React + Babel CDN app |

No new containers. No npm. No build step. The existing docker-compose stack is unchanged.

### REST API (added to server.py)

All endpoints share the existing DB connection pool and embedder. CORS is not needed (same-origin).

| Endpoint | Method | Purpose | Backed by |
|---|---|---|---|
| `/ui` | GET | Serve `ui.html` | Static file read |
| `/api/memories` | GET | Paginated list. Params: `project`, `tag`, `since`, `before`, `limit`, `offset` | `list_memories` logic |
| `/api/memories/:id` | GET | Single memory detail | `get_memory` logic |
| `/api/memories/:id/related` | GET | Nearest neighbors via cosine sim. Param: `limit` (default 3) | `semantic_search` on content |
| `/api/recall` | POST | Semantic search. Body: `{query, threshold}` | `semantic_search` logic |
| `/api/projects` | GET | Distinct project names + memory counts | DB query |
| `/api/tags` | GET | All tags with counts (active only) | `list_tags` logic |
| `/api/stats` | GET | Memory counts, storage, session import status | `get_stats` logic |
| `/api/preferences` | GET | Memories tagged `type:preference` or `type:pattern`, grouped by category tag | `list_memories` filtered |
| `/api/memories` | DELETE | Bulk soft-delete. Params: `project`, `tag` | `bulk_delete` logic |

Error shape: `{"error": "<message>"}` with appropriate HTTP status.

---

## Data Mapping

The design handoff uses richer "session" concepts. Here is how each maps to real `memories` table fields:

| Design concept | Real data |
|---|---|
| Session title | `content` (first 72 chars, truncated with `…`) |
| Session topic / timeline lane | `project` (one lane per distinct project value) |
| Session date | `created_at` |
| Session duration / dot size | `len(content)` (chars), scaled 6–20px radius |
| Session "turns" count | `len(tags)` |
| Session summary | `content` (full text) |
| Key moments | Related memories from `/api/memories/:id/related` (nearest neighbors), rendered with colored left-border by their `type:*` tag |
| Files touched | Tags prefixed `file:` (rendered as mono chips) |
| Tag pills | All tags not prefixed `file:` |
| Transcript overlay | Renamed "Full Memory" — full `content` text in a modal reader |
| Preferences section | Memories tagged `type:preference` or `type:pattern`, grouped by their category tag |
| Similarity score / bar | Cosine similarity from pgvector (`1 - embedding <=> query_vec`) |

### Key-moment kind → color mapping
Inferred from the memory's `type:*` tag. Fallback to `decision` styling.

| Tag | Color | Background |
|---|---|---|
| `type:decision` | `#009CFF` | `rgba(0,156,255,0.07)` |
| `type:bug` / `type:fix` | `#1FA890` | `rgba(31,168,144,0.10)` |
| `type:pattern` | `#312888` | `rgba(49,40,136,0.05)` |
| `type:preference` | `#7A4DFF` | `rgba(122,77,255,0.07)` |
| `type:warning` / gotcha | `#FF947B` | `rgba(255,148,123,0.12)` |

---

## UI Sections (top → bottom)

### 1. Sticky App Header
- Logo: inline SVG brain mark (left hemisphere indigo, right cyan) + "CLAUDE MEMORY" wordmark (Mulish 800, 16px, 0.16em tracking, UPPERCASE)
- Nav: Timeline · Search · Preferences · Settings — scroll-spy or click-to-scroll (no routing)
- Right: health pill (green dot + "idx healthy · {latency}ms"), "local" label
- Sticky `top:0`, `z-index:5`, 1px subtle bottom border

### 2. Title Row + Stat Strip
- Eyebrow "PERSISTENT MEMORY" + H1 "Recall everything." + Cardo italic "*Across every session.*"
- Stat strip: 6 cells divided by 1px subtle vertical rules, 2px indigo top rule
- Stats sourced from `GET /api/stats`: Memories stored · Chars embedded · Active projects · p99 recall · Cos threshold · Retention

### 3. Search Bar
- 1.5px indigo border, lucide search icon, text input, mono "↵" hint
- Right of input: `{N} hits · {latency}ms · cos≥0.78`
- Below: "Try:" eyebrow + 4 hardcoded suggestion chips (populated from handoff; can be made dynamic later)
- On submit: `POST /api/recall {query, threshold: 0.78}` → updates search results + clears selected memory

### 4. Timeline River
- SVG, one horizontal lane per project (up to 8 lanes; overflow goes to "other")
- X-axis: last 21 days (or range of `created_at` in DB if < 21 days of data)
- Dots: circle per memory, color = project color (assigned from a fixed 8-color palette), radius scales 6–16px by `len(content)`, white 2px stroke
- Selected dot: 3px stroke + outer ring
- Bezier river curves through each lane's dots at opacity 0.14 stroke
- Topic filter pills: "All" + one per project — filter = show only that project's dots
- Click dot → set `selectedId`, scroll detail pane into view
- Data from `GET /api/memories?since=21d&limit=500` (enough for timeline; no pagination needed)

### 5. Two-Column Body — Search Results + Memory Detail

**Left — Search Results**
- Header: "SEARCH RESULTS" eyebrow + mono current query
- Each result row: topic dot + project eyebrow + mono `{id} · {date}` + 64×3 similarity bar + score
- Title: first 72 chars of content, 15px/700 indigo (active = cyan)
- Snippet: 13px italic, ~120 chars from content around the match
- Click row → set `selectedId`
- Empty state: dashed border, "No matches above 0.78 similarity. Try a broader query."
- On page load (no query): show `GET /api/memories?limit=20` most recent memories as the default list

**Right — Memory Detail**
- Project eyebrow + mono `memory://{id}`
- Title (22px/700 indigo) = first 72 chars of content
- 4-column meta strip (Project · Date · Length · Tags) with 2px indigo top rule
- Full content paragraph (14px, line-height 1.6)
- "RELATED MEMORIES" eyebrow + up to 3 related memory cards from `/api/memories/:id/related`, rendered with colored left-border by `type:*` tag
- File tags: mono chips for `file:*` tags
- Tag pills: remaining tags (1.5px cyan border, 18px radius)
- Actions: "Read full memory →" (opens MemoryReader overlay) + "Copy ID" (copies `memory://{id}` to clipboard)
- Empty state when no memory selected: indigo-light bg, "Select a memory from the timeline or search results."

### 6. Preferences Section
- Section background `--cs-grey-light`
- Header: "LEARNED PREFERENCES" eyebrow + H2 "What Claude has noticed about *how you work.*"
- Data: `GET /api/preferences` → memories tagged `type:preference` or `type:pattern`
- Group by the memory's most specific category tag (e.g. `category:code-style`, `category:stack`, `category:workflow`, `category:decision`). If no category tag, group under "General"
- Each card: white bg, 1px subtle border, card title = category name
- Each item row: content text + confidence bar (use `updated_at` recency as proxy for confidence: updated in last 7d = 95%, 30d = 80%, older = 65%) + source tag
- Empty state: "Run `extract_signals.py` to populate inferred preferences." with mono code hint

### 7. Settings / Data Controls Section
- 2×2 grid of cards
- **Retention**: radio options 30d / 90d / 1yr / Forever. Active state persisted in `localStorage` (no backend setting yet — future `PATCH /api/settings`)
- **Storage**: `GET /api/stats` → total memories, estimated size (rough: avg 500 bytes/embedding × count). Breakdown by category: Embeddings (indigo), Transcripts (cyan), Metadata (coral)
- **What's stored**: toggle rows for Memory content / Embedding vectors / Tags. Persisted in `localStorage`. "Secrets & tokens" row is locked (coral, cursor not-allowed)
- **Danger zone**: coral 2px border
  - Export → `GET /api/memories?format=json` download
  - Delete project → prompt for project name → `DELETE /api/memories?project={name}`
  - Wipe all → confirm dialog → `DELETE /api/memories` (no filters = all)

### 8. Memory Reader Overlay
- Triggered by "Read full memory →" in detail pane
- Backdrop: `rgba(14,10,61,0.55)`, full-viewport, `z-index:50`
- Panel: white, max-width 760px, `--shadow-lg`, 24px×28px padding
- Sticky header: "FULL MEMORY · {id}" eyebrow, title (22px/700), mono meta line, close button
- Body: full `content` rendered as markdown (use `marked.js` CDN for simple markdown rendering)
- Tags displayed below content
- Related memories list (same as detail pane)
- Close: click backdrop or close button; ESC key

### 9. Footer
- 2px indigo top border
- Three mono spans: `memory.local · running locally` · `{N} memories · 768-dim vectors · {size}` · `docs · ⌘K search · feedback`

---

## State Management

Single top-level React state object:

```js
{
  section: 'timeline' | 'search' | 'preferences' | 'settings',  // active nav
  selectedId: number | null,       // currently focused memory id
  query: string,                   // current search query
  filter: string | 'all',          // project filter for timeline
  readerOpen: boolean,             // full memory overlay
  searchResults: Memory[],         // from POST /api/recall
  memories: Memory[],              // timeline + default list
  projects: Project[],             // from GET /api/projects
  stats: Stats,                    // from GET /api/stats
  preferences: PrefGroup[],        // from GET /api/preferences
  loading: boolean,
  error: string | null,
}
```

---

## Component Structure (in ui.html)

All components defined as React functions in a single `<script type="text/babel">` block.

```
App
├── AppHeader
├── TitleRow
├── StatStrip
├── SearchBar
├── TimelineRiver          (SVG, pure derived from memories + filter)
├── TwoColBody
│   ├── SearchResults
│   └── MemoryDetail
│       └── RelatedMemories
├── PreferencesSection
│   └── PrefCard (×N)
├── SettingsSection
│   ├── RetentionCard
│   ├── StorageCard
│   ├── WhatStoredCard
│   └── DangerZoneCard
├── MemoryReader (overlay)
└── Footer
```

Shared primitives (defined once, used throughout):
- `MeterBar` — renders a colored bar given `value` (0–1) and `color`
- `Eyebrow` — 11px/700 uppercase tracked label in cyan
- `TagPill` — 1.5px cyan border, 18px radius pill

---

## Design Tokens

Implemented as CSS custom properties at `:root` in `ui.html`:

```css
--cs-blue:       #312888;
--cs-blue-hover: #241b7b;
--cs-blue-light: #009CFF;
--cs-blue-dark:  #1a1560;
--cs-coral:      #FF947B;
--cs-white:      #ffffff;
--cs-grey-light: #f8f7fc;
--cs-grey:       #6b6b78;
--cs-ink:        #1a1b1c;
--fg-1:          var(--cs-blue);
--fg-2:          #3d3d4a;
--fg-3:          var(--cs-grey);
--fg-accent:     var(--cs-blue-light);
--border-subtle: rgba(49,40,136,0.12);
--border-rule:   var(--cs-blue);
--shadow-xs:     0 1px 3px rgba(26,27,28,0.06);
--shadow-sm:     0 2px 8px rgba(26,27,28,0.08);
--shadow-lg:     0 30px 60px -20px rgba(50,50,93,0.25), 0 18px 36px -18px rgba(0,0,0,0.30);
```

Project color palette (8 colors, assigned in order to distinct `project` values):
`#312888`, `#009CFF`, `#7A4DFF`, `#FF947B`, `#1FA890`, `#D9325A`, `#F5A623`, `#4A90D9`

---

## CDN Dependencies (in ui.html `<head>`)

```html
<script src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script src="https://unpkg.com/marked/marked.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800;900&family=Cardo:ital@1&display=swap" rel="stylesheet">
```

---

## Accessibility

- `role="dialog"` + `aria-modal="true"` + focus trap on MemoryReader overlay
- ESC closes overlay
- ⌘K / Ctrl+K focuses search input
- `aria-label` on all icon-only buttons
- `tabIndex` and `onKeyDown` (Enter/Space) on interactive river dots and result rows
- `role="button"` where non-button elements are clickable

---

## Error & Empty States

| Scenario | Behavior |
|---|---|
| DB unavailable | Red banner at top: "Database unreachable. Check docker compose is running." |
| Search returns 0 results | Dashed-border empty state with query echo |
| No preferences data | Empty state with `extract_signals.py` hint |
| Memory has no related items | "No related memories found." muted text |
| Timeline has < 2 memories per project | Dots only, no bezier curve (need ≥ 2 points) |

---

## Out of Scope

- Authentication (this is a local-only tool)
- Realtime updates / WebSockets (manual refresh sufficient)
- Mobile responsive layout
- Full-text keyword search UI (semantic search via `/api/recall` is the primary path)
- Editing memories in the UI (use MCP tools for writes)
