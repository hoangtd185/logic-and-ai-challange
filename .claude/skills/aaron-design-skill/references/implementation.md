# Implementation — fonts, setup, shared systems

Applies to both aesthetics. Read alongside the chosen culture's reference file.

## Importing fonts

### Pretendard (Korean) — CDN
```html
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@latest/dist/web/variable/pretendardvariable.min.css">
```
```css
body { font-family: "Pretendard Variable", Pretendard, -apple-system,
  "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }
```

### Inter / Geist (Western SaaS) — Google Fonts / CDN
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
```
Geist: `https://cdn.jsdelivr.net/npm/geist@latest/dist/...` or the `geist` npm
package in a React/Next project.

### SF Pro (Apple/editorial)
SF Pro isn't freely web-licensed for non-Apple sites; `-apple-system` /
`BlinkMacSystemFont` renders it natively on Apple devices, with
`"Helvetica Neue", Arial` as the cross-platform fallback. For a closer match on
all platforms, **Inter** is the safest substitute.

## Tailwind setup (if the project uses Tailwind)

Map the chosen reference file's tokens into `tailwind.config`:
```js
// Korean example
theme: {
  extend: {
    fontFamily: { sans: ['"Pretendard Variable"', 'Pretendard', 'sans-serif'] },
    borderRadius: { lg:'16px', xl:'20px', '2xl':'24px' },
    boxShadow: { card:'0 6px 20px rgba(0,0,0,0.05)', soft:'0 8px 24px rgba(0,0,0,0.08)' },
    colors: {
      gray: { 900:'#191F28', 700:'#4E5968', 500:'#8B95A1', 300:'#D1D6DB',
              100:'#F2F4F6', 50:'#F9FAFB' },
      accent: '#3182F6',
      mint: '#1D9E75',
    },
  },
}
```
For Western SaaS, swap to the `international.md` Track A values (zinc grays,
indigo accent, 6–10px radii, border-led instead of shadow-heavy).

## Plain CSS variables (framework-agnostic)

Define the tokens once at `:root`, then build with them. Drop in the block from
the relevant reference file (`korean.md` or `international.md`) verbatim — they
are already written as `--var: value;` declarations.

## Dark mode

- **Korean fintech**: usually light-first; if dark is needed, deepen neutrals
  (bg `#17171C`, surface `#1F1F24`), keep the accent, lighten text. Keep the
  soft-shadow / big-radius grammar.
- **Western SaaS (Track A)**: often dark-FIRST. Build dark as the primary, light
  as the variant. Use the dark token block in `international.md` directly.
- Always test every color in both modes — text on tinted fills is the usual
  failure point. Never hardcode `#000`/`#333`; use variables.

## Build-quality checklist (both aesthetics)

- [ ] Correct font loaded and actually applied to `body` (the #1 authenticity
      signal — verify it's not silently falling back).
- [ ] Radius scale matches the culture (large for Korean, small/med for SaaS).
- [ ] Shadow grammar matches (soft-diffuse vs border-led vs near-none).
- [ ] Exactly one dominant accent color.
- [ ] Spacing rhythm consistent and generous enough for the culture.
- [ ] Microcopy tone matches (warm/personal for Korean, crisp for SaaS, sparse
      for editorial).
- [ ] Works in both light and dark mode without hardcoded colors.
- [ ] Mobile layout intact (Korean is mobile-first; verify single-column flow).

## Combining with the frontend-design skill

`frontend-design` governs *how to build well* in this environment (component
patterns, the environment's styling constraints). This skill governs *what the
result should look like culturally*. Read both: build with frontend-design's
mechanics, paint with this skill's tokens. When the environment forbids
something a culture relies on (e.g. a flat sandbox that disallows shadows),
approximate the feel with the levers you do have — larger radii, tinted fills,
spacing — and note the gap.