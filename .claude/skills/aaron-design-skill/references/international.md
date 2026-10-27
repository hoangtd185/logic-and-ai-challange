# International / Western aesthetic — "gu quốc tế" tokens

Two distinct tracks dominate. Pick one — they don't mix well.

- **Track A — Tech / SaaS** (Linear, Stripe, Vercel, Raycast): sharp,
  high-contrast, grid-disciplined, often dark-mode-first, bold confident type.
- **Track B — Premium / editorial** (Apple): spacious, very large type, calm,
  full-bleed imagery, near-monochrome with restraint.

---

## Track A — Tech / SaaS

### Font
**Inter** is the default; **Geist** (Vercel) and **SF Pro** are common too.
Medium weights, not heavy. Subtle negative letter-spacing on headings.
```css
font-family: "Inter", "Geist", -apple-system, BlinkMacSystemFont,
  "Segoe UI", Roboto, sans-serif;
```
Headings 600 with `letter-spacing: -0.02em`. Body 400–450, ~15–16px.

### Color — high contrast, dark-mode-friendly
Near-black surfaces, crisp white text, one saturated accent.
```
/* Dark (primary for this track) */
--bg:        #0A0A0A   or  #09090B (zinc-950)
--surface:   #18181B   (zinc-900)
--border:    #27272A   (zinc-800)
--text:      #FAFAFA
--text-mut:  #A1A1AA   (zinc-400)
--accent:    #6366F1   (indigo) | #3B82F6 (blue) | #8B5CF6 (violet)

/* Light variant */
--bg:        #FFFFFF
--surface:   #FAFAFA
--border:    #E4E4E7
--text:      #09090B
--text-mut:  #71717A
```
Linear-style gradients are allowed here but kept subtle and dark.

### Radius — small to medium
```
--radius-sm: 6px   --radius-md: 8px   --radius-lg: 10px   --radius-xl: 12px
```
Sharper than Korean. Big pill buttons feel off in this track.

### Shadow — minimal, border-led
SaaS leans on **1px borders** more than shadows. When shadows appear they're
tight and crisp, or replaced by subtle ring/glow on focus.
```
--shadow-sm: 0 1px 2px rgba(0,0,0,0.06);
--shadow-md: 0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06);
--ring:      0 0 0 1px rgba(99,102,241,0.4);  /* focus / accent ring */
```

### Layout
Grid-disciplined, denser than Korean, comfortable with information. Sticky top
nav, hero with tight headline + subtext + dual CTA (filled accent + ghost),
feature grid (2–3 col on desktop), logo cloud, code blocks, precise alignment.
Dark mode is a first-class citizen, not an afterthought.

### Mood
Confident, technical, fast. Microcopy is crisp and direct, not cute. Bold
oversized headlines, generous but efficient spacing.

---

## Track B — Premium / editorial (Apple-like)

### Font
Large SF Pro / Helvetica-style sans, or a refined serif for editorial moments.
```css
font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial,
  sans-serif;
```
Display sizes are *large* (48–96px headlines), weights range thin→semibold,
tight letter-spacing on big type.

### Color — restrained, mostly monochrome
```
--bg:      #FFFFFF  (and #000000 for dramatic dark sections)
--text:    #1D1D1F  (Apple near-black)
--text-mut:#6E6E73
--surface: #F5F5F7  (Apple's signature light gray)
--accent:  #0071E3  (Apple blue) — used sparingly, mostly on links/CTAs
```
Color comes from full-bleed photography/product imagery, not UI chrome.

### Radius — medium, smooth
```
--radius-md: 12px   --radius-lg: 18px   --radius-xl: 28px (large feature cards)
--radius-button: 980px  /* Apple's fully-rounded pill buttons */
```

### Shadow — almost none
Depth comes from spacing and imagery, not shadows. Occasional very soft
`0 4px 24px rgba(0,0,0,0.06)` under a floating product shot.

### Layout
Extremely generous whitespace. Full-bleed sections alternating light/dark.
Centered hero: huge headline + one-line subhead + a pill CTA. Big product
imagery. Lots of vertical breathing room. Minimal nav. Everything calm and
deliberate.

### Mood
Premium, calm, confident. Few words, large and well-spaced. Nothing rushed.

---

## Picking between tracks

| If the product is…                    | Use      |
|---------------------------------------|----------|
| Dev tool, B2B SaaS, dashboard, API    | Track A  |
| Consumer hardware, luxury, brand site | Track B  |
| Startup wanting "modern + sharp"      | Track A  |
| Anything wanting "premium + spacious" | Track B  |

## Anti-patterns

- Mixing Track A density with Track B whitespace → muddy, unfocused.
- Big soft Korean-style shadows in Track A → looks dated/wrong.
- Many accent colors → both tracks want restraint (one accent).
- Track B with small cramped type → loses the premium feel entirely.
- Default Times/Arial without intentional type choice → reads unfinished.