# Refined Glass Redesign — Design Spec

**Date:** 2026-06-12
**Target:** `src/cuepoint/templates/report.html` (+ minor `src/cuepoint/html_creator.py` support)
**Approach:** A — layered evolution. Keep the existing Vue 3 logic and DOM structure; swap the design-token layer, restyle components, add a detail view and a motion system. Not a rewrite.

## 1. Goal

Restyle the interactive HTML report into an iOS-native-feeling "Refined Glass" UI: glassmorphism on a dark base, ambient color glows, app-like mobile feed, and a new event detail view that explains *why* an event matches the user. All existing functionality (search, filters, sort, table view, .ics export, feedback loop, static fallback) is preserved.

Reference mockups (approved): `.superpowers/brainstorm/1285-1781263819/content/glass-mockup-v2.html` (mobile), `glass-mockup-desktop.html` (desktop).

## 2. Constraints

- **CSP is unchanged.** `font-src 'none'` → system font stack only (`-apple-system, 'Segoe UI', Roboto, …`); no webfont, no icon font (inline SVG as today). `img-src https: data:`, `connect-src` substituted per config — all current substitution markers (`__EVENTS_DATA__`, `__API_BASE__`, `__CSP_CONNECT_SRC__`, `__STATIC_FALLBACK__`, `__STATS_FOOTER__`, `__VUE_RUNTIME__`) keep working.
- **Single self-contained file.** Everything inline in `report.html`; no new assets.
- **Static fallback stays.** `_build_static_fallback()` output renders inside the same page; it must remain legible under the new CSS (it reuses `.table-wrap`, table styles, `.genre-pill`, `.artist-row`, `.floor-label`).
- **Feedback loop untouched.** `setFeedback`, `syncFeedback`, localStorage queue, Went/Skipped buttons keep their current logic and test coverage (`tests/test_html_feedback.py`).
- **`prefers-reduced-motion` respected** — the existing global kill-switch rule stays and all new animation goes through it.

## 3. Design language (token layer)

Replace the `:root` block values; keep variable *names* where they already exist so component CSS keeps resolving, and add new tokens.

| Token | Value | Notes |
|---|---|---|
| `--bg-primary` | `#0b0d14` | page base |
| `--glass` | `rgba(255,255,255,0.05)` | default frosted surface |
| `--glass-strong` | `rgba(255,255,255,0.09)` | elevated surface (toolbar, sheet, modal) |
| `--glass-border` | `rgba(255,255,255,0.10)` | 1px hairline on glass |
| `--accent` | `#4a9eff` | unchanged |
| `--purple` | `#a855f7` | brighter than current `#c678dd` |
| `--grad-score` | `linear-gradient(135deg, #4a9eff, #a855f7)` | score badge, primary CTAs, bar fills |
| `--radius-pill` | `999px` | buttons, chips, badges |
| `--radius-card` | `20px` | cards, sheets, modals |
| `--blur` | `blur(24px) saturate(1.4)` | backdrop-filter for glass surfaces |

**Ambient glows:** two fixed-position radial gradients behind all content (a `body::before`/`::after` pair or a single fixed layer): violet `radial-gradient(600px 480px at 85% -5%, rgba(88,60,200,0.22), transparent 60%)` top-right, blue equivalent (`rgba(40,90,200,0.15)`) lower-left. `pointer-events: none; z-index: -1`.

**Glass recipe** (shared utility class or mixin pattern): `background: var(--glass); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); border: 1px solid var(--glass-border); border-radius: …`. Applied to: toolbar, genre panel, cards, bottom action bar, detail sheet/modal, dropdowns.

**Typography:** system stack only. Slightly larger titles (card title 17–18px, detail title 20–22px), `-0.3px`-ish negative tracking on headings, `font-variant-numeric: tabular-nums` kept for numbers.

## 4. Components

### 4.1 Toolbar (desktop) / bottom action bar (mobile)

- **Desktop (≥768px):** existing sticky toolbar restyled to glass; same controls (brand, search, Followed-only, genre dropdown, view toggle, .ics export, count). View toggle **stays** — table view is explicitly kept on desktop.
- **Mobile (<768px):** toolbar collapses to brand + count. Controls move into a **fixed bottom glass action bar** with three items: **Search**, **Filter**, **.ics**. `padding-bottom: env(safe-area-inset-bottom)` aware.
  - *Search* expands an input inline in the bar (or focuses a revealed field above the bar); Esc/blur collapses it.
  - *Filter* opens the genre chips + Followed-only as a small glass sheet anchored to the bar (replaces the current horizontal `genre-chips` strip as the primary entry; the strip itself is removed on mobile).
  - *.ics* triggers `exportICS()` as today.

### 4.2 Event card (both breakpoints; the only mobile layout)

- **Flyer is the hero:** full-bleed `card-flyer` image, top corners rounded `--radius-card`, gradient scrim bottom→up (as today, tuned darker: `rgba(8,9,14,0.94)` base). No-flyer events get the violet/navy gradient placeholder (`linear-gradient(160deg,#3d2a6e 0%,#1b2440 55%,#0e1626 100%)`).
- **Score badge:** gradient pill (`--grad-score`), top-right over the flyer, white bold `matchPct` value, soft glow shadow `0 2px 10px rgba(120,90,255,0.5)`. Tapping it opens the detail view (replaces the inline `scoreExpanded` toggle in card view).
- **Overlay content:** date·time, title, venue + attending, genre pills — same data, glassier pills.
- **Card body:** tickets and the truncated lineup, always visible on both breakpoints. The mobile `cardExpanded` accordion and `card-expand-hint` button are removed — card click (any breakpoint) opens the **detail view** instead (clicks on links/buttons excluded, same `closest('a, button, input')` guard).
- **Lineup truncation on card:** show top-3 artists by **relevance order** (see 4.5) + a "+N more" passive label (not a toggle — full lineup lives in the detail view). Floor labels shown only for the artists displayed.
- **Went/Skipped buttons remain on the card overlay** (existing behavior + tests), restyled as glass pills.
- **Mobile feed:** single column, full-width cards, 20px gap. Desktop grid: `auto-fill minmax(340px, 1fr)` as today.

### 4.3 Table view (desktop only, kept)

- Visible only ≥768px; the view toggle persists in the toolbar. Below 768px the card feed is the only layout (current responsive table-to-card CSS at `max-width: 768px` is deleted — cards replace it).
- Glass restyle: translucent header, hairline row borders, hover glow, followed rows keep the accent edge.
- **Row click opens the detail modal** (same guard for links/buttons). Inline `scoreExpanded` breakdown in the title cell is removed in favor of the modal; Went/Skipped stay inline in the title cell.

### 4.4 Detail view (new)

One Vue-managed component instance, two presentations:

- **Mobile:** bottom sheet sliding up over a dimmed, blurred backdrop; drag-handle bar at top; max-height ~92dvh, scrollable.
- **Desktop:** centered modal, max-width ~880px, two columns — flyer left (~40%), content right.

**State:** `detailEvent = ref(null)`; open via card/row click or score-badge tap; close via ✕ button, backdrop click, or Esc. Body scroll locked while open.

**Content (top to bottom on mobile; right column on desktop):**
1. Flyer hero at top of sheet (mobile); desktop: left column, full height.
2. Title, date/time, venue (linked), attending, genre pills.
3. **"Why this matches you" panel** — glass panel with gradient tint border:
   - Score badge (large gradient pill, `matchPct`).
   - Plain-language reasons from `ev.briefing` (already computed server-side).
   - **Score contribution bars** from `ev.scoreBreakdown`: label + animated gradient bar (width = `value / score`, capped 100%) + value, reusing current breakdown data. Followed/rising keep their distinct bar colors.
4. **Lineup** — relevance-ordered (4.5), floor grouping kept. Show top-6 with full stats (SC/DC/BC/RA, country, rising ⚡, similarity, shared labels, expandable tag chips — all existing renderers); "Show all N" button expands the rest in place.
5. **Tickets** — same list/sold-out rendering as cards.
6. **Action row** (sticky bottom of sheet on mobile): **Went**, **Skipped** (wired to existing `setFeedback`), **+ Calendar** (`downloadSingleICS`), **RA ↗** (event URL). Glass pills; Went/Skipped reflect `feedback[ev.id]` active state.

### 4.5 Lineup relevance ordering

New computed helper `rankedArtists(ev)` (pure JS, client-side — no Python changes):
1. Followed artists first (`isFollowed`).
2. Then rising (`rising`).
3. Then by `scFollowers` descending.
Stable within ties (original lineup order). Floor labels render per displayed artist (label shown when it differs from the previous displayed artist's floor) — floor grouping survives as labels, but ordering is relevance-first.

Card: `rankedArtists(ev).slice(0, 3)` + "+N more". Detail: `slice(0, 6)` + "Show all N" toggle (`ev.lineupExpanded` reused).

### 4.6 iOS home-screen support

Add meta tags: `apple-mobile-web-app-capable`, `apple-mobile-web-app-status-bar-style: black-translucent`, `theme-color #0b0d14`. (No icon asset — self-contained constraint; omit `apple-touch-icon`.)

## 5. Motion system

Rich but tasteful; every rule inside the existing `prefers-reduced-motion` kill-switch.

| Motion | Spec |
|---|---|
| Card entrance | staggered fade+rise (`opacity 0→1`, `translateY 12px→0`), ~40ms stagger via `animation-delay` (cap stagger at first ~15 cards), 350ms ease-out |
| Press feedback | buttons/cards `scale(0.97)` on `:active`, ~120ms |
| Sheet open/close | mobile: translateY 100%→0, 320ms `cubic-bezier(0.32, 0.72, 0, 1)`; desktop modal: fade + scale 0.96→1, 220ms; backdrop fade |
| Score bars | width animates 0→target on detail open (CSS transition triggered after mount) |
| View/filter changes | existing Vue list reflow kept; no FLIP work added |

Stagger implementation: CSS-only via inline `animation-delay` bound to the `v-for` index — no JS animation library.

## 6. Data flow

No new server-side fields. Everything the detail view needs is already in the event JSON: `briefing`, `scoreBreakdown`, `score`, `matchPct`, `artists[]` (with `isFollowed`, `rising`, `scFollowers`, stats, tags), `tickets`, `flyer`, `promoters`, `genres`, `attending`.

`html_creator.py` changes are limited to whatever the template restructure requires (expected: none beyond keeping substitution markers intact). `_build_static_fallback()` unchanged.

## 7. Error handling / resilience

- No-flyer events: gradient placeholder in card and detail.
- Empty `briefing`/`scoreBreakdown`: "Why this matches you" panel collapses to just the score badge; if `matchPct` is 0/absent, panel hidden entirely.
- Empty tickets: section omitted (as today).
- Vue mount failure: static fallback still shows (unchanged mechanism).
- `backdrop-filter` unsupported (old browsers): glass surfaces degrade to their rgba background — keep rgba alphas dark enough that text stays readable without blur.

## 8. Testing

- **Existing suite must stay green** (626 tests). `tests/test_html_feedback.py` exercises feedback DOM/JS — Went/Skipped classes (`fb-btn`, `went`, `skipped`, `active`) and handlers are preserved.
- **New template assertions** (extend existing HTML-creator tests): detail-view markup present; substitution markers intact; iOS meta tags present.
- **Headless Playwright smoke test** (same approach as the feedback-loop round-trip test): open report, click a card → detail opens with briefing text and breakdown bars; Esc closes; mobile viewport (390×844) → bottom action bar visible, table absent; desktop viewport → table toggle works, row click opens modal; Went click in detail syncs feedback POST; `rankedArtists` ordering (followed > rising > followers) verified in-page.
- Manual: iOS Safari home-screen add, reduced-motion mode.

## 9. Out of scope

- No scoring/learning changes, no API changes, no new Python modules.
- No icon assets / PWA manifest.
- No table view on mobile.
- No virtualized list (event counts are small).
