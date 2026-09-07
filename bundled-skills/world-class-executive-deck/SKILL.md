---
name: world-class-executive-deck
description: World-class executive/leadership presentation design — synthesized from McKinsey Pyramid + SCQA, Nancy Duarte Resonate (What-Is / What-Could-Be sparkline), Apple Keynote reductionism, BCG action-title convention, Edward Tufte data density. Use when the user needs a leadership-facing deck that must feel "world-class", "high-end", "not AI-slop", and must survive scrutiny from senior executives. Complements pptx-advanced (layout safety) with narrative + hierarchy + taste rules.
version: 1.0.0
---

# World-Class Executive Deck

The five schools we synthesize:
- **McKinsey Pyramid + SCQA** — one governing thought at top, mutually exclusive collectively exhaustive support below
- **Nancy Duarte Resonate** — "What is / What could be" sparkline oscillation; audience is the hero, presenter is the mentor
- **Apple Keynote reductionism** — one idea per slide, giant typography, blackspace as a weapon
- **BCG/Bain action-title convention** — every slide title is the *takeaway sentence*, not the topic
- **Edward Tufte** — maximize data-ink ratio; every pixel earns its place

---

## The Seven Non-Negotiable Rules

### 1. Every slide title is an ACTION TITLE

Wrong: "评测组现状"
Right: "评测组已用 Agent 把 5 周的评测压到 2 周，节省 22 人力"

The reader who scrolls only the titles must be able to reconstruct your entire argument. This is the single biggest quality gap between consultant decks and internal decks.

### 2. Governing thought first, then MECE support

Structure the whole deck as a pyramid:
- Top: the ONE sentence you want leadership to remember
- L2: 3-5 mutually exclusive, collectively exhaustive support pillars
- L3: evidence for each pillar (numbers, screenshots, cases)

If two slides support the same pillar with overlapping content, merge or cut. **Overlapping content is a signal your pyramid is not MECE.**

### 3. What-Is / What-Could-Be sparkline (Duarte)

Every content slide should oscillate between reality and aspiration:
- "Today we do X (pain)" → "With Agent we do Y (gain)"
- "Old workflow: 5 people × 2 weeks" → "New workflow: 2 people × 3 days"

Contrast is what makes leadership feel the story. Flat state descriptions bore executives.

### 4. One idea per slide (Apple)

If a slide has more than one takeaway, it has zero takeaways. Cut ruthlessly. It is always better to have 40 focused slides than 25 crowded ones.

### 5. Real artifacts > redrawn cards

**This is the specific failure mode we're fixing:** when the source material contains real screenshots / architecture diagrams / example outputs, EMBED THEM. Do not re-draw them as pptxgenjs shapes. A real V5 platform screenshot beats any hand-rendered "V5 module grid" card.

If real artifacts are unavailable, say so on the page ("架构图待补 · 已联系 XX") rather than fake it with generic shapes.

### 6. Data ink ratio (Tufte)

Every shape, line, gradient, glow must earn its place by carrying information. Decoration for decoration is AI-slop.

Kill: aurora glows without purpose, purple bars that don't index anything, "megaNumber" chapter watermarks that don't reinforce the pyramid.
Keep: bar chart, sparkline, comparison table, real screenshot, one-color highlight of the takeaway number.

### 7. Executive summary slide is mandatory

Slide 2 (right after cover) must be a single-slide executive summary that a busy leader can read in 30 seconds and walk away with the full argument. Everything after it is proof.

Format:
```
[Action title: the ONE sentence]
────────
[Pillar 1 headline]    [Pillar 2 headline]    [Pillar 3 headline]
[1-line evidence]      [1-line evidence]      [1-line evidence]
────────
[Ask: what leadership decision you want]
```

---

## Structural Anti-Patterns to Delete on Sight

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Topic titles ("XX 组画像") | Reader learns nothing from scanning | Rewrite as action title with the takeaway |
| Two frameworks describing the same journey (e.g. "三步演化" + "三阶段路线图") | Reader gets confused which is past vs future | Merge into ONE timeline with past/present/future stripes |
| Grand summary table at the end | Repeats numbers already shown, feels like padding | Move it to slide 2 as the executive summary |
| Chapter dividers with big numbers but no takeaway | Wastes a whole slide on ornament | Put the chapter's action title on the divider itself |
| "Thanks" + "The End" + "Q&A" separate slides | Deck ends on filler | One closing slide that repeats the governing thought + the ask |

---

## Concrete Layout Patterns (proven on leadership decks)

### Pattern A — Executive summary (slide 2)
- Action title full width
- 3-column pillar cards; each card has 1 headline + 1 number + 1 evidence line
- Bottom strip: "领导层需要拍板的一件事" (the ask)

### Pattern B — What-is / What-could-be (per team)
- Left half: OLD workflow diagram (grey/muted colors, real screenshots if available)
- Right half: NEW workflow diagram (brand color, real screenshots)
- Bottom: single line showing the delta (人力 -22 / 周期 -75%)

### Pattern C — Real screenshot as hero
- Slide title = action title
- Body = one big real screenshot with 3-4 callouts pointing to key features
- Right rail: 3 bullets explaining what leadership should notice

### Pattern D — Sparkline case study
- Small chart top-left showing the metric trend
- Right column: story in 3 short paragraphs
- No decoration. Data speaks.

### Pattern E — Governing thought (chapter divider)
- Black slide
- Big serif quote of the chapter's action title
- Small line below: "支撑证据 · 3 slides"

---

## Typography — Editorial, Not Corporate

- Display: Source Han Serif SC / Noto Serif SC — for hero titles only, 60-90pt
- Sans: Source Han Sans SC / PingFang SC — for action titles + body, 14-36pt
- Mono: JetBrains Mono / Consolas — for numbers ONLY
- Never mix 4+ font families. Never use decorative fonts.
- Line-height 1.35-1.5 for body. Kerning +2 to +6 for uppercase Latin.

## Color — Restraint Over Rainbow

- Pick ONE hero color for the "gain" side of every comparison
- Pick ONE muted color for "pain" / baseline
- Reserve red for negative deltas ONLY; never use red as a general accent
- Chapter dividers black. Content slides deep-charcoal or off-white. Choose one, don't mix.

## The "Would McKinsey Ship This?" Test

Before declaring a slide done, ask:
1. Does the title state a takeaway, not a topic?
2. Would a leader who reads only titles get the full story?
3. Is there real proof (screenshot, chart, quote) — not redrawn shapes?
4. Would removing anything make it stronger? (If yes, remove it.)
5. Is there a single dominant visual, not 4 competing ones?

If any answer is NO, iterate.

---

## Verification

Use pptx-advanced skill for safe-zone / overflow checks. Then run this narrative QA:

- **Title-only scroll test**: extract just the titles into a numbered list; if a colleague can reconstruct the argument, pass
- **30-second summary test**: show slide 2 (executive summary) alone; if a leader gets the point, pass
- **One-idea-per-slide test**: for each content slide, write down its takeaway in one sentence; if you can't, split the slide
- **Real-vs-fake test**: for each diagram/chart, verify it's from the source system (screenshot / real export), not redrawn as generic shapes
