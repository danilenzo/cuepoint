# Refined Glass Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Vue 3 HTML report (`src/cuepoint/templates/report.html`) into the approved "Refined Glass" design: glassmorphism tokens, ambient glows, app-like mobile feed with bottom action bar, and a new event detail view (mobile bottom sheet / desktop modal) with a "Why this matches you" panel.

**Architecture:** Approach A — layered evolution. All changes live in the single self-contained template `report.html` plus new pytest assertions. The Vue setup() logic gains a detail-view state (`detailEvent`), a lineup relevance sorter (`rankedArtists`), and mobile bottom-bar state; existing logic (filters, sort, feedback, .ics) is untouched. No Python data changes — `briefing`, `scoreBreakdown`, and artist fields are already in the event JSON.

**Tech Stack:** Vue 3 (vendored, CSP-compatible), inline CSS custom properties, pytest (template string assertions), Playwright (headless smoke script, manual run).

**Spec:** `docs/superpowers/specs/2026-06-12-glass-redesign-design.md`

---

## File Structure

| File | Change |
|---|---|
| `src/cuepoint/templates/report.html` | All UI work: tokens, glows, glass recipe, card/table restyle, detail view, bottom bar, motion |
| `tests/test_glass_template.py` | New — template assertions per task (tokens, meta tags, detail markup, rankedArtists, bottom bar) |
| `scripts/smoke_glass_report.py` | New — headless Playwright smoke script (manual verification, not part of pytest suite) |
| `src/cuepoint/html_creator.py` | No changes expected; substitution markers must stay intact |

Run the full suite with `python -m pytest tests/ -q` (626 tests green before starting; must stay green after every task).

**Conventions used throughout:**
- New tests use the existing fixtures `sample_artist_info` and `mock_config` plus the helper `tests.conftest._make_event_row` (see `tests/test_html_feedback.py` for the pattern).
- All template edits preserve the substitution markers: `/* __VUE_RUNTIME__ */`, `"__EVENTS_DATA__"`, `"__API_BASE__"`, `__CSP_CONNECT_SRC__`, `<!-- __STATIC_FALLBACK__ -->`, `<!-- __STATS_FOOTER__ -->`.
- Feedback DOM contract preserved: classes `fb-btn`, `went`, `skipped`, `active`; functions `setFeedback`, `syncFeedback`; localStorage key `cuepoint_feedback`.

---

### Task 1: Design-token layer, ambient glows, glass recipe

**Files:**
- Modify: `src/cuepoint/templates/report.html` (`:root` block ~lines 9–38, `body` rule ~lines 40–50)
- Test: `tests/test_glass_template.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_glass_template.py`:

```python
"""Template assertions for the Refined Glass redesign."""

import pandas as pd

from cuepoint.html_creator import create_html
from tests.conftest import _make_event_row


def _render(sample_artist_info):
    df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
    return create_html(df)


class TestGlassTokens:
    def test_new_tokens_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        for token in ("--glass:", "--glass-strong:", "--glass-border:",
                      "--grad-score:", "--radius-pill:", "--radius-card:", "--blur:"):
            assert token in html, f"missing token {token}"

    def test_base_palette(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "#0b0d14" in html          # new page base
        assert "#a855f7" in html          # new purple
        assert "rgba(88,60,200,0.22)" in html  # violet ambient glow

    def test_substitution_markers_survive(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert '"__EVENTS_DATA__"' not in html
        assert "__CSP_CONNECT_SRC__" not in html
        assert "/* __VUE_RUNTIME__ */" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py -q`
Expected: FAIL — `missing token --glass:` (markers test passes already).

- [ ] **Step 3: Update the `:root` block**

In `report.html`, replace the `:root` block with (keep every existing variable name; change values, add new ones):

```css
:root {
    --bg-primary: #0b0d14;
    --bg-secondary: #11141c;
    --bg-card: rgba(255,255,255,0.05);
    --bg-elevated: rgba(255,255,255,0.07);
    --bg-hover: rgba(255,255,255,0.10);
    --border: rgba(255,255,255,0.07);
    --border-hover: rgba(255,255,255,0.14);
    --glass: rgba(255,255,255,0.05);
    --glass-strong: rgba(255,255,255,0.09);
    --glass-border: rgba(255,255,255,0.10);
    --text-primary: #eef0f6;
    --text-secondary: #8d94ab;
    --text-muted: #5a617a;
    --accent: #4a9eff;
    --accent-dim: rgba(74,158,255,0.12);
    --accent-glow: rgba(74,158,255,0.25);
    --green: #7ec87e;
    --green-dim: rgba(126,200,126,0.10);
    --red: #e06c75;
    --red-dim: rgba(224,108,117,0.12);
    --purple: #a855f7;
    --orange: #d19a66;
    --cyan: #56b6c2;
    --grad-score: linear-gradient(135deg, #4a9eff, #a855f7);
    --radius: 16px;
    --radius-sm: 10px;
    --radius-xs: 6px;
    --radius-pill: 999px;
    --radius-card: 20px;
    --blur: blur(24px) saturate(1.4);
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
    --shadow-md: 0 4px 16px rgba(0,0,0,0.35);
    --shadow-lg: 0 8px 32px rgba(0,0,0,0.45);
    --shadow-badge: 0 2px 10px rgba(120,90,255,0.5);
    --transition-fast: 150ms ease;
    --transition-base: 200ms ease;
}
```

- [ ] **Step 4: Switch the font stack and add ambient glows**

In the `body` rule, change the font-family line to the system stack (CSP `font-src 'none'` — Inter never loaded anyway):

```css
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
```

Immediately after the `body { ... }` rule add:

```css
body::before, body::after {
    content: ''; position: fixed; inset: 0;
    pointer-events: none; z-index: -1;
}
body::before {
    background: radial-gradient(600px 480px at 85% -5%, rgba(88,60,200,0.22), transparent 60%);
}
body::after {
    background: radial-gradient(700px 540px at -10% 85%, rgba(40,90,200,0.15), transparent 60%);
}
.glass {
    background: var(--glass);
    backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
    border: 1px solid var(--glass-border);
}
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_glass_template.py tests/test_html_feedback.py tests/test_html_functions.py -q`
Expected: PASS. Then full suite: `python -m pytest tests/ -q` — all green.

- [ ] **Step 6: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): glass design tokens, ambient glows, system font stack"
```

---

### Task 2: iOS home-screen meta tags

**Files:**
- Modify: `src/cuepoint/templates/report.html` (`<head>`, after the viewport meta ~line 6)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_glass_template.py`:

```python
class TestIOSMeta:
    def test_home_screen_meta_tags(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
        assert 'apple-mobile-web-app-status-bar-style" content="black-translucent"' in html
        assert '<meta name="theme-color" content="#0b0d14">' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py::TestIOSMeta -q`
Expected: FAIL.

- [ ] **Step 3: Add the meta tags**

After the viewport `<meta>` in `report.html`:

```html
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0d14">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_glass_template.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): iOS home-screen meta tags"
```

---

### Task 3: Lineup relevance ordering (`rankedArtists`) + card top-3 "+N more"

**Files:**
- Modify: `src/cuepoint/templates/report.html` (Vue setup() ~line 1136 near `toggleCard`; card-lineup template block ~lines 855–885)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_glass_template.py`:

```python
class TestRankedArtists:
    def test_ranked_artists_function_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "function rankedArtists(" in html
        assert "cardArtists" in html

    def test_card_lineup_uses_ranked_top3(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "cardArtists(ev)" in html
        assert "lineup-more" in html  # passive "+N more" label class
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py::TestRankedArtists -q`
Expected: FAIL.

- [ ] **Step 3: Add the JS helpers**

In setup(), next to `toggleCard`, add:

```js
// Relevance order: followed > rising > everyone; SC followers desc inside
// each tier; original lineup order for exact ties.
function rankedArtists(ev) {
    const tier = a => a.isFollowed ? 0 : (a.rising ? 1 : 2);
    return ev.artists.map((a, i) => [a, i])
        .sort((x, y) =>
            (tier(x[0]) - tier(y[0])) ||
            ((y[0].scFollowers || 0) - (x[0].scFollowers || 0)) ||
            (x[1] - y[1]))
        .map(p => p[0]);
}
function cardArtists(ev) { return rankedArtists(ev).slice(0, 3); }
```

Add `rankedArtists, cardArtists` to the `return { ... }` object of setup().

- [ ] **Step 4: Rewire the card lineup block**

Replace the card-view `.card-lineup` inner template (the `v-for` over `ev.lineupExpanded || ev.artists.length <= 5 ? ...` plus the `lineup-toggle` button) with:

```html
<template v-for="(a, idx) in cardArtists(ev)" :key="idx">
    <div class="floor-label" v-if="a.floor && (idx === 0 || a.floor !== cardArtists(ev)[idx-1].floor)">{{ a.floor }}</div>
    <div class="artist-row">
        <template v-if="a.scUrl">
            <a :href="a.scUrl"><b v-if="a.isFollowed">{{ a.name }}</b><span v-else>{{ a.name }}</span></a>
        </template>
        <span v-else>{{ a.name }}</span>
        <span class="artist-stats" v-if="a.scFollowers || a.dcHave || a.bcUrl">
            <template v-if="a.scFollowers">
                <span :class="a.isFollowed ? 'stat-val followed' : 'stat-label'">SC</span>
                <span class="stat-val">{{ fmtNum(a.scFollowers) }}</span>
            </template>
            <template v-if="a.dcHave"> &middot; <span class="stat-label">DC</span> <span class="stat-val">{{ fmtNum(a.dcHave) }}</span></template>
            <template v-if="a.bcUrl">
                &middot; <a :href="a.bcUrl" class="stat-label">BC</a>
                <span v-if="a.bcSupporters" class="stat-val">{{ fmtNum(a.bcSupporters) }}</span>
            </template>
        </span>
        <span v-if="a.rising" class="rising-badge" title="Rising artist">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        </span>
        <span v-if="a.similarTo" class="sim-badge">~ {{ a.similarTo }}</span>
    </div>
</template>
<div class="lineup-more" v-if="ev.artists.length > 3">+{{ ev.artists.length - 3 }} more</div>
```

Add the CSS near `.lineup-toggle`:

```css
.lineup-more {
    font-size: 12px; color: var(--text-muted); padding: 6px 0 0;
    text-align: center;
}
```

(Keep `.lineup-toggle` CSS and the table-view lineup block as-is — the table lineup stays untouched; the detail view in Task 4 takes over full-lineup duty for cards.)

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): relevance-ranked card lineup, top-3 + passive more-count"
```

---

### Task 4: Detail view — state, markup, open/close wiring

**Files:**
- Modify: `src/cuepoint/templates/report.html` (setup() state; end of `#app` markup; card/table click handlers; CSS)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_glass_template.py`:

```python
class TestDetailView:
    def test_detail_markup_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "detail-backdrop" in html
        assert "detail-panel" in html
        assert "Why this matches you" in html
        assert "openDetail" in html
        assert "closeDetail" in html

    def test_detail_action_row(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "detail-actions" in html
        # feedback contract intact
        assert "setFeedback" in html
        assert "fb-btn" in html

    def test_card_accordion_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "card-expand-hint" not in html
        assert "cardExpanded" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py::TestDetailView -q`
Expected: FAIL.

- [ ] **Step 3: Add detail state + handlers to setup()**

```js
// --- detail view ---
const detailEvent = ref(null);
const detailBarsOn = ref(false);
function openDetail(ev) {
    detailEvent.value = ev;
    detailBarsOn.value = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => requestAnimationFrame(() => { detailBarsOn.value = true; }));
}
function closeDetail() {
    detailEvent.value = null;
    document.body.style.overflow = '';
}
const detailArtists = computed(() => {
    const ev = detailEvent.value;
    if (!ev) return [];
    const ranked = rankedArtists(ev);
    return ev.lineupExpanded ? ranked : ranked.slice(0, 6);
});
function onEventClick(ev, e) {
    if (e.target.closest('a, button, input')) return;
    openDetail(ev);
}
```

Extend the existing keydown listener with Esc support (add as the first line inside the handler, before the INPUT/TEXTAREA early-return, so Esc closes the sheet even while the search input is focused):

```js
if (e.key === 'Escape' && detailEvent.value) { closeDetail(); return; }
```

Add `detailEvent, detailBarsOn, detailArtists, openDetail, closeDetail, onEventClick` to setup()'s return object.

- [ ] **Step 4: Rewire openers, remove the accordion**

1. Card root: replace `@click="toggleCard(ev, $event)"` with `@click="onEventClick(ev, $event)"`.
2. Table row: add `@click="onEventClick(ev, $event)"` to the `<tr v-for="ev in filteredEvents" ...>`.
3. Match badge (card + table): replace the `@click`/`@click.stop` `scoreExpanded` toggles with `@click.stop="openDetail(ev)"`.
4. Delete: `card-expand-hint` button markup, `card-body-wrap` `:class="{collapsed: ...}"` binding (keep the wrapper div), `toggleCard` function, all `cardExpanded` references (template + the `eventsData` map initializer), `scoreExpanded` initializer and both inline `v-if="ev.scoreExpanded"` breakdown blocks (table title cell and card body), `.card-expand-hint` and `.card-body-wrap.collapsed` CSS.
5. Remove `toggleCard` from the return object; keep `formatTickets` etc. unchanged.

- [ ] **Step 5: Add the detail markup**

Just before `</div>` closing `#app` (after the card grid), add:

```html
<!-- Detail view: bottom sheet (mobile) / centered modal (desktop) -->
<div class="detail-backdrop" v-if="detailEvent" @click.self="closeDetail()">
    <div class="detail-panel glass" role="dialog" aria-modal="true" :aria-label="detailEvent.title">
        <button class="detail-close" @click="closeDetail()" aria-label="Close">&#10005;</button>
        <div class="detail-handle" aria-hidden="true"></div>
        <div class="detail-flyer" :class="{'no-flyer': !detailEvent.flyer}">
            <img v-if="detailEvent.flyer" :src="detailEvent.flyer" :alt="detailEvent.title">
        </div>
        <div class="detail-content">
            <div class="detail-header">
                <div class="detail-date">{{ formatDate(detailEvent.startTime) }} &middot; {{ formatTimeRange(detailEvent.startTime, detailEvent.endTime) }}</div>
                <h2 class="detail-title">{{ detailEvent.title }}</h2>
                <div class="detail-venue">
                    <a :href="detailEvent.venueUrl">{{ detailEvent.venueName }}</a>
                    <span v-if="detailEvent.attending" style="opacity:0.6"> &middot; {{ fmtNum(detailEvent.attending) }} going</span>
                </div>
                <div class="detail-genres" v-if="detailEvent.genres.length">
                    <span v-for="g in detailEvent.genres" :key="g.name" :class="'genre-pill ' + g.category">{{ g.name }}</span>
                </div>
            </div>

            <div class="why-panel" v-if="detailEvent.matchPct">
                <div class="why-head">
                    <span class="why-title">Why this matches you</span>
                    <span class="score-badge">{{ detailEvent.matchPct }}%</span>
                </div>
                <div class="briefing" v-if="detailEvent.briefing.length">
                    <span v-for="(r, i) in detailEvent.briefing" :key="i" class="briefing-reason">{{ r }}</span>
                </div>
                <div class="score-breakdown" v-if="detailEvent.scoreBreakdown.length">
                    <div class="sb-row" v-for="s in detailEvent.scoreBreakdown" :key="s.key">
                        <span class="sb-label">{{ s.label }}</span>
                        <span class="sb-bar"><span class="sb-bar-fill" :class="s.key"
                            :style="{width: (detailBarsOn ? Math.min(100, s.value / detailEvent.score * 100) : 0) + '%'}"></span></span>
                        <span class="sb-val">{{ fmtNum(s.value) }}</span>
                    </div>
                </div>
            </div>

            <div class="detail-lineup">
                <template v-for="(a, idx) in detailArtists" :key="idx">
                    <div class="floor-label" v-if="a.floor && (idx === 0 || a.floor !== detailArtists[idx-1].floor)">{{ a.floor }}</div>
                    <div class="artist-row">
                        <template v-if="a.scUrl">
                            <a :href="a.scUrl"><b v-if="a.isFollowed">{{ a.name }}</b><span v-else>{{ a.name }}</span></a>
                        </template>
                        <span v-else>{{ a.name }}</span>
                        <span class="artist-stats" v-if="a.scFollowers || a.dcHave || a.bcUrl || a.raFollowers">
                            <template v-if="a.scFollowers">
                                <span :class="a.isFollowed ? 'stat-val followed' : 'stat-label'">SC</span>
                                <span class="stat-val">{{ fmtNum(a.scFollowers) }}</span>
                            </template>
                            <template v-if="a.dcHave"> &middot; <span class="stat-label">DC</span> <span class="stat-val">{{ fmtNum(a.dcHave) }}</span> <span style="opacity:0.4">r{{ a.dcRatio }}</span></template>
                            <template v-if="a.bcUrl">
                                &middot; <a :href="a.bcUrl" class="stat-label">BC</a>
                                <span v-if="a.bcSupporters" class="stat-val">{{ fmtNum(a.bcSupporters) }}</span>
                                <span v-if="a.bcLatestRelease" style="opacity:0.4;font-size:0.85em;"> {{ a.bcLatestRelease }}</span>
                            </template>
                            <template v-if="a.raFollowers"> &middot; <span style="color:var(--orange);">RA {{ fmtNum(a.raFollowers) }}</span></template>
                        </span>
                        <i v-if="a.country" style="color:var(--text-muted);"> ({{ a.country }})</i>
                        <span v-if="a.rising" class="rising-badge" title="Rising artist">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        </span>
                        <span v-if="a.similarTo" class="sim-badge" :title="a.similarityScore + '% similar'">~ {{ a.similarTo }}</span>
                        <span v-if="a.sharedLabels.length" class="label-badge">[{{ a.sharedLabels.join(', ') }}]</span>
                        <button class="artist-expand-btn" v-if="a.tags.length" @click="a.expanded = !a.expanded" :aria-expanded="a.expanded" aria-label="Toggle tags">tags</button>
                        <div class="artist-detail" v-if="a.expanded">
                            <div class="tag-list"><span class="tag-chip" v-for="t in a.tags" :key="t">{{ t }}</span></div>
                        </div>
                    </div>
                </template>
                <button v-if="detailEvent.artists.length > 6" class="lineup-toggle" @click="detailEvent.lineupExpanded = !detailEvent.lineupExpanded">
                    {{ detailEvent.lineupExpanded ? 'Show less' : 'Show all ' + detailEvent.artists.length }}
                </button>
            </div>

            <div class="card-tickets" v-if="detailEvent.tickets && detailEvent.tickets.length">
                <template v-if="detailEvent.tickets.every(t => t.soldOut)">
                    <span class="sold-out">SOLD OUT</span>
                </template>
                <template v-else>
                    <div class="ticket-row" v-for="t in detailEvent.tickets.filter(t => !t.soldOut).sort((a, b) => a.price - b.price)" :key="t.title">
                        <span class="ticket-name">{{ cleanTicketTitle(t.title) }}</span>
                        <span class="ticket-price">{{ t.symbol }}{{ t.price.toFixed(2) }}</span>
                    </div>
                </template>
            </div>

            <div class="detail-actions">
                <button class="fb-btn went" :class="{active: feedback[detailEvent.id] === 'went'}" @click="setFeedback(detailEvent, 'went')">Went</button>
                <button class="fb-btn skipped" :class="{active: feedback[detailEvent.id] === 'skipped'}" @click="setFeedback(detailEvent, 'skipped')">Skipped</button>
                <button class="act-btn" @click="downloadSingleICS(detailEvent)">+ Calendar</button>
                <a class="act-btn" :href="detailEvent.eventUrl">RA &#8599;</a>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 6: Add detail CSS**

Add after the `.empty-state` rules:

```css
/* --- detail view --- */
.detail-backdrop {
    position: fixed; inset: 0; z-index: 50;
    background: rgba(8,9,14,0.6);
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    display: flex; align-items: flex-end; justify-content: center;
}
.detail-panel {
    background: rgba(20,23,33,0.92);
    width: 100%; max-height: 92dvh; overflow-y: auto;
    border-radius: var(--radius-card) var(--radius-card) 0 0;
    position: relative;
    padding-bottom: env(safe-area-inset-bottom);
}
.detail-handle {
    width: 38px; height: 4px; border-radius: 2px;
    background: rgba(255,255,255,0.25);
    margin: 10px auto 0;
}
.detail-close {
    position: absolute; top: 12px; right: 12px; z-index: 2;
    width: 32px; height: 32px; border-radius: var(--radius-pill);
    background: rgba(8,9,14,0.55); color: var(--text-primary);
    border: 1px solid var(--glass-border); cursor: pointer;
    font-size: 13px; font-family: inherit;
}
.detail-flyer img { width: 100%; display: block; border-radius: 0; max-height: 48vh; object-fit: cover; }
.detail-flyer.no-flyer {
    height: 120px;
    background: linear-gradient(160deg,#3d2a6e 0%,#1b2440 55%,#0e1626 100%);
}
.detail-content { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 16px; }
.detail-date { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
.detail-title { font-size: 21px; font-weight: 700; letter-spacing: -0.3px; margin: 4px 0; }
.detail-venue { font-size: 13px; color: var(--text-secondary); }
.detail-genres { margin-top: 8px; }
.why-panel {
    background: linear-gradient(135deg, rgba(74,158,255,0.10), rgba(168,85,247,0.10));
    border: 1px solid rgba(120,140,255,0.2);
    border-radius: var(--radius); padding: 12px 14px;
}
.why-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.why-title { font-size: 13px; font-weight: 700; }
.score-badge {
    background: var(--grad-score); color: #fff;
    border-radius: var(--radius-pill); padding: 3px 12px;
    font-size: 13px; font-weight: 800; box-shadow: var(--shadow-badge);
    font-variant-numeric: tabular-nums;
}
.why-panel .sb-bar-fill { transition: width 600ms cubic-bezier(0.22, 1, 0.36, 1); }
.detail-actions {
    display: flex; gap: 8px; flex-wrap: wrap;
    position: sticky; bottom: 0; padding: 12px 0 4px;
    background: linear-gradient(transparent, rgba(20,23,33,0.95) 35%);
}
.detail-actions .fb-btn, .detail-actions .act-btn {
    flex: 1; min-height: 44px; font-size: 13px; font-weight: 600;
    border-radius: var(--radius-pill); border: 1px solid var(--glass-border);
    background: var(--glass-strong); color: var(--text-primary);
    cursor: pointer; font-family: inherit; text-align: center;
    display: inline-flex; align-items: center; justify-content: center;
}
@media (min-width: 768px) {
    .detail-backdrop { align-items: center; padding: 24px; }
    .detail-panel {
        max-width: 880px; max-height: 86vh;
        border-radius: var(--radius-card);
        display: grid; grid-template-columns: 40% 1fr;
    }
    .detail-handle { display: none; }
    .detail-flyer { grid-row: 1; }
    .detail-flyer img { height: 100%; max-height: none; border-radius: var(--radius-card) 0 0 var(--radius-card); }
    .detail-flyer.no-flyer { height: 100%; border-radius: var(--radius-card) 0 0 var(--radius-card); }
    .detail-content { overflow-y: auto; max-height: 86vh; }
}
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all green (`cardExpanded` removal must not break `tests/test_html_feedback.py`).

- [ ] **Step 8: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): event detail view with why-this-matches panel"
```

---

### Task 5: Card + table glass restyle, gradient score badge

**Files:**
- Modify: `src/cuepoint/templates/report.html` (CSS only: `.event-card`, `.match-badge`, table, toolbar, pills, `.fb-btn`)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the regression-guard tests**

Append to `tests/test_glass_template.py`:

```python
class TestGlassRestyle:
    def test_gradient_match_badge(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "var(--grad-score)" in html

    def test_zebra_striping_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "tbody tr:nth-child(even)" not in html
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_glass_template.py::TestGlassRestyle -q`
Expected: `test_gradient_match_badge` passes (Task 4 CSS already uses it); `test_zebra_striping_removed` FAILS.

- [ ] **Step 3: Restyle CSS (no DOM changes)**

Apply these CSS edits:

1. `.event-card`: `background: var(--glass); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); border: 1px solid var(--glass-border); border-radius: var(--radius-card);` (keep overflow/flex/transition lines).
2. `.event-card .card-flyer.no-flyer`: background → `linear-gradient(160deg,#3d2a6e 0%,#1b2440 55%,#0e1626 100%);`
3. `.card-flyer::after` scrim base color → `rgba(8,9,14,0.94)`.
4. `.match-badge` high/mid/low variants replaced by one gradient style:
```css
.match-badge {
    display: inline-flex; align-items: center;
    padding: 3px 10px; border-radius: var(--radius-pill);
    font-size: 12px; font-weight: 800;
    font-variant-numeric: tabular-nums; letter-spacing: -0.3px;
    cursor: pointer; transition: opacity var(--transition-fast);
    background: var(--grad-score); color: #fff;
    box-shadow: var(--shadow-badge);
}
.match-badge:hover { opacity: 0.85; }
.match-badge.low { opacity: 0.55; box-shadow: none; }
```
(Keep `matchClass()` JS and the `high`/`mid`/`low` class bindings — `high`/`mid` simply no longer have overrides.)
5. `.fb-btn`: `border-radius: var(--radius-pill); background: var(--glass); min-height: 28px; padding: 2px 10px;` — keep class names and active variants exactly.
6. Toolbar: `background: rgba(11,13,20,0.7); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); border-bottom: 1px solid var(--glass-border);`
7. Table: `th` background → `rgba(11,13,20,0.85); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);`; remove zebra striping rules (the four `tbody tr:nth-child(even)` rules); row hover stays. Add `tbody tr { cursor: pointer; }`.
8. `.genre-panel`: add `backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); background: rgba(20,23,33,0.92);`
9. Buttons (`.tb-action`, `.view-toggle`, `.ics-export`, `.genre-dropdown-btn`, `.genre-chip`, `.lineup-toggle`): `border-radius: var(--radius-pill);`
10. `.card-overlay-title` base font-size 16px → 17px (mobile 18px override stays).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "style(report): glass restyle for cards, table, toolbar, badges"
```

---

### Task 6: Mobile — cards only, bottom action bar, filter sheet

**Files:**
- Modify: `src/cuepoint/templates/report.html` (mobile CSS block `@media (max-width: 768px)`; toolbar markup; new bottom-bar markup; setup() state)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_glass_template.py`:

```python
class TestMobileBottomBar:
    def test_bottom_bar_markup(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "bottom-bar" in html
        assert "mobileSearchOpen" in html
        assert "mobileFilterOpen" in html

    def test_mobile_table_css_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        # the old table-to-block transform must be gone
        assert "table, thead, tbody, tr, th, td { display: block" not in html
        assert "attr(data-label)" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py::TestMobileBottomBar -q`
Expected: FAIL.

- [ ] **Step 3: Force card view on mobile in JS**

In setup(), replace `function onResize() { ... }` with:

```js
function onResize() {
    isMobile.value = window.innerWidth <= 768;
    if (isMobile.value && viewMode.value === 'table') viewMode.value = 'card';
}
```

Add state:

```js
const mobileSearchOpen = ref(false);
const mobileFilterOpen = ref(false);
```

Export `mobileSearchOpen, mobileFilterOpen` from setup()'s return object.

- [ ] **Step 4: Add bottom bar + filter sheet markup**

Before the detail-view block inside `#app`:

```html
<!-- Mobile bottom action bar -->
<div class="bottom-bar glass" v-if="isMobile">
    <button class="bar-btn" :class="{active: mobileSearchOpen}" @click="mobileSearchOpen = !mobileSearchOpen; mobileFilterOpen = false">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        Search
    </button>
    <button class="bar-btn" :class="{active: mobileFilterOpen || selectedGenres.length || followedOnly}" @click="mobileFilterOpen = !mobileFilterOpen; mobileSearchOpen = false">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M7 12h10M10 18h4"/></svg>
        Filter{{ selectedGenres.length ? ' (' + selectedGenres.length + ')' : '' }}
    </button>
    <button class="bar-btn" @click="exportICS">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        .ics
    </button>
</div>
<div class="bar-search glass" v-if="isMobile && mobileSearchOpen">
    <input type="text" v-model="searchQuery" placeholder="Search artists, venues..."
           @keydown.escape="mobileSearchOpen = false" aria-label="Search events">
</div>
<div class="bar-filter glass" v-if="isMobile && mobileFilterOpen">
    <label class="bar-filter-row"><input type="checkbox" v-model="followedOnly"> Followed only</label>
    <div class="bar-filter-chips">
        <button v-for="g in topGenres" :key="g.name" class="genre-chip"
                :class="[{active: selectedGenres.includes(g.name)}, g.category]"
                :aria-pressed="selectedGenres.includes(g.name)"
                @click="toggleGenreChip(g.name)">{{ g.name }}</button>
        <button v-if="selectedGenres.length" class="genre-chip" style="color:var(--red);" @click="selectedGenres = []">Clear</button>
    </div>
</div>
```

Delete the old strip markup: the `<div class="genre-chips" v-if="topGenres.length">` block after the toolbar.

- [ ] **Step 5: CSS — bottom bar, kill old mobile table CSS**

Add (base styles, outside media queries):

```css
/* --- mobile bottom action bar --- */
.bottom-bar {
    display: none; position: fixed; left: 12px; right: 12px;
    bottom: calc(10px + env(safe-area-inset-bottom)); z-index: 40;
    border-radius: var(--radius-pill); padding: 6px;
    background: rgba(20,23,33,0.8);
    gap: 6px;
}
.bar-btn {
    flex: 1; min-height: 44px; border: none; background: transparent;
    color: var(--text-secondary); font-family: inherit; font-size: 12px;
    font-weight: 600; border-radius: var(--radius-pill); cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    touch-action: manipulation; -webkit-tap-highlight-color: transparent;
}
.bar-btn svg { width: 15px; height: 15px; }
.bar-btn.active { background: var(--accent-dim); color: var(--accent); }
.bar-search, .bar-filter {
    display: none; position: fixed; left: 12px; right: 12px;
    bottom: calc(68px + env(safe-area-inset-bottom)); z-index: 40;
    border-radius: var(--radius); padding: 10px;
    background: rgba(20,23,33,0.92);
}
.bar-search input {
    width: 100%; background: var(--glass); color: var(--text-primary);
    border: 1px solid var(--glass-border); border-radius: var(--radius-pill);
    padding: 10px 14px; font-size: 14px; outline: none;
}
.bar-filter-row { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 4px 4px 10px; }
.bar-filter-chips { display: flex; flex-wrap: wrap; gap: 6px; max-height: 40vh; overflow-y: auto; }
```

In the `@media (max-width: 768px)` block:
1. **Delete** the entire table-to-block transform: every rule from `table, thead, tbody, tr, th, td { display: block; ... }` through the `td[data-label=...]` rules, plus `.genre-chips { display: flex; }` and `.genre-dropdown { display: none; }`.
2. **Add**:
```css
.table-wrap { display: none; }
.bottom-bar { display: flex; }
.bar-search, .bar-filter { display: block; }
.toolbar .search-wrap, .toolbar > label, .toolbar .view-toggle,
.toolbar .ics-export, .toolbar .genre-dropdown { display: none; }
.card-grid { padding-bottom: 90px; }
```
(`v-if` controls actual visibility of bar-search/bar-filter; the `display` rule only un-hides them at this breakpoint.)
3. **Keep**: single-column `.card-grid`, card overlay size overrides, toolbar padding rules.
4. Also delete the now-orphaned base strip-container CSS: `.genre-chips { display: none; ... }` and its two scrollbar rules — keep the `.genre-chip` button styles (reused in the filter sheet).

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): mobile bottom action bar, cards-only mobile layout"
```

---

### Task 7: Motion system

**Files:**
- Modify: `src/cuepoint/templates/report.html` (CSS; card `v-for` index for stagger)
- Test: `tests/test_glass_template.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_glass_template.py`:

```python
class TestMotion:
    def test_entrance_animation_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "@keyframes card-in" in html
        assert "animationDelay" in html

    def test_reduced_motion_guard_kept(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "prefers-reduced-motion" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_glass_template.py::TestMotion -q`
Expected: FAIL on `card-in`.

- [ ] **Step 3: Add motion CSS**

```css
/* --- motion --- */
@keyframes card-in {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
}
.event-card { animation: card-in 350ms ease-out backwards; }
@keyframes sheet-in {
    from { transform: translateY(100%); }
    to { transform: translateY(0); }
}
@keyframes modal-in {
    from { opacity: 0; transform: scale(0.96); }
    to { opacity: 1; transform: scale(1); }
}
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
.detail-backdrop { animation: fade-in 200ms ease-out; }
.detail-panel { animation: sheet-in 320ms cubic-bezier(0.32, 0.72, 0, 1); }
@media (min-width: 768px) {
    .detail-panel { animation: modal-in 220ms ease-out; }
}
.event-card:active { transform: scale(0.98); }
.bar-btn:active, .detail-actions .fb-btn:active, .detail-actions .act-btn:active { transform: scale(0.95); }
```

(The existing `prefers-reduced-motion` rule already zeroes `animation-duration` globally — covers all of the above.)

- [ ] **Step 4: Stagger via inline delay**

Change the card-grid `v-for` to expose the index and bind the delay (cap at 15):

```html
<div class="event-card" v-for="(ev, evIdx) in filteredEvents" :key="ev.id"
     :class="{followed: ev.hasFollowed}"
     :style="{animationDelay: Math.min(evIdx, 15) * 40 + 'ms'}"
     @click="onEventClick(ev, $event)">
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/cuepoint/templates/report.html tests/test_glass_template.py
git commit -m "feat(report): motion system — staggered entrance, sheet/modal transitions"
```

---

### Task 8: Playwright smoke script + full verification

**Files:**
- Create: `scripts/smoke_glass_report.py`
- Test: manual run (not part of pytest suite — Playwright is not a test dependency; keeps the 626-test suite hermetic)

- [ ] **Step 1: Write the smoke script**

```python
"""Headless smoke test for the Refined Glass report.

Manual verification (Playwright required):
    pip install playwright && playwright install chromium
    python scripts/smoke_glass_report.py
"""

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from cuepoint.html_creator import create_html  # noqa: E402
from tests.conftest import _make_event_row  # noqa: E402


def make_artist(name, followers, rising=False):
    return {
        "id": name, "name": name,
        "soundcloud": None,
        "sc_followers": followers,
        "sc_tags": json.dumps(["Techno"]),
        "_rising": rising,
    }


def main():
    # nobody followed -> riser (tier 1) must outrank both tier-2 artists,
    # including the one with 90k followers
    artists = [
        make_artist("low-nobody", 100),
        make_artist("big-nobody", 90000),
        make_artist("riser", 500, rising=True),
    ]
    row = _make_event_row("evt-1", artists, score=120, notable=2, total=3)
    row["_match_pct"] = 87
    row["_briefing"] = ["1 rising artist on the lineup", "Strong techno match"]
    row["_score_breakdown"] = {"sc_followers": 80.0, "rising": 25.0, "ra_genre": 15.0}
    html = create_html(pd.DataFrame([row]))

    out = Path(tempfile.mkdtemp()) / "report.html"
    out.write_text(html, encoding="utf-8")
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- mobile ---
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(out.as_uri())
        page.wait_for_selector(".event-card")
        if not page.is_visible(".bottom-bar"):
            failures.append("mobile: bottom bar not visible")
        if page.is_visible(".table-wrap table"):
            failures.append("mobile: table visible")
        names = page.eval_on_selector_all(
            ".event-card .card-lineup .artist-row", "els => els.map(e => e.textContent.trim())")
        if not names or "riser" not in names[0]:
            failures.append(f"mobile: ranked order wrong, first row = {names[:1]}")
        page.click(".event-card .card-body")
        page.wait_for_selector(".detail-panel")
        if "Why this matches you" not in page.inner_text(".detail-panel"):
            failures.append("mobile: why-panel missing")
        if not page.is_visible(".detail-actions .fb-btn.went"):
            failures.append("mobile: detail actions missing")
        page.keyboard.press("Escape")
        page.wait_for_selector(".detail-panel", state="detached")
        page.close()

        # --- desktop ---
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(out.as_uri())
        page.wait_for_selector(".toolbar")
        if not page.is_visible("table"):
            failures.append("desktop: table view not default")
        # click the time cell — row center may land on a link/button (guard ignores those)
        page.click("tbody tr td:first-child")
        page.wait_for_selector(".detail-panel")
        page.click(".detail-close")
        page.wait_for_selector(".detail-panel", state="detached")
        page.click(".view-toggle")
        page.wait_for_selector(".event-card")
        page.close()
        browser.close()

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("PASS: glass report smoke test")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke script**

Run: `python scripts/smoke_glass_report.py`
Expected: `PASS: glass report smoke test`. Fix any failure in the template before proceeding (debug with `p.chromium.launch(headless=False)` if needed).

- [ ] **Step 3: Full suite + lint**

Run: `python -m pytest tests/ -q` — all green.
Run: `ruff check src/ scripts/` and `ruff format --check src/ scripts/` — clean (fix if not).

- [ ] **Step 4: Generate a real report for eyeball check**

If cached data exists: `python -m cuepoint.event_fetcher --cities berlin --days 7`, open the `output/` HTML, verify: glass look, glows, card stagger, detail sheet (devtools mobile emulation), bottom bar, table view + row-click modal on desktop, static fallback legible with JS disabled.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_glass_report.py
git commit -m "test(report): headless Playwright smoke script for glass redesign"
```

---

## Self-Review Checklist (run after Task 8)

- [ ] Spec section 2 constraints: CSP untouched, single file, static fallback renders (check `static-view` div with JS disabled), feedback contract classes intact.
- [ ] Spec section 4.5: ranked ordering verified by smoke script.
- [ ] Old artifacts fully gone: `cardExpanded`, `card-expand-hint`, `scoreExpanded` inline breakdowns, mobile table-to-block CSS, genre-chips strip.
- [ ] `python -m pytest tests/ -q` green; `ruff check src/ scripts/` clean.
- [ ] Use superpowers:finishing-a-development-branch for merge/PR decision.
