---
name: page-design-aesthetics
description: >-
  Design and build web pages, landing pages, app screens, and UI components in a
  specific market/cultural aesthetic — especially Korean style ("gu Hàn", like
  Toss, Kakao, Naver, Baemin, 29CM, Musinsa) or international/Western style ("gu
  quốc tế", like Linear, Stripe, Vercel, or Apple). Use this skill WHENEVER a
  design or front-end request mentions a cultural style, a reference brand's
  look, "gu Hàn", "gu Hàn Quốc", "Korean style", "gu quốc tế", "Western",
  "SaaS look", or asks for a page/screen/component that should "look like" a
  particular market — even if no framework is named. Covers the exact palette,
  typography, font stacks, spacing, border-radius, shadow, and layout tokens for
  each aesthetic so output reads authentically native to that market instead of
  generic. Pair with the frontend-design skill for the build itself.
---

# Page Design Aesthetics

Generic UI looks like nobody made it. This skill encodes the *specific* visual
grammar of two design cultures — Korean and international/Western — so that what
you build reads as authentically belonging to that market, not as a default
template with a different accent color.

The single biggest lever is not "which CSS" but **how precisely the brief names
the aesthetic.** Before building anything, lock down the style direction.

## Step 1 — Identify the aesthetic direction

Resolve these before writing any code. If the user already gave a reference
brand or a one-word mood, you have enough — infer the rest and state your
assumptions inline. Only ask if direction is genuinely missing.

1. **Which culture?** Korean ("gu Hàn") or international/Western ("gu quốc tế")?
2. **Which sub-variant?**
   - Korean → *fintech/utility* (Toss, KakaoPay, Baemin) vs *lifestyle/commerce*
     (29CM, Musinsa, Oliveyoung — more editorial, image-led).
   - International → *tech/SaaS* (Linear, Stripe, Vercel — sharp, high-contrast,
     often dark) vs *premium/editorial* (Apple — spacious, large type, calm).
3. **Surface?** Landing page (web) or app screen (mobile)? This changes layout,
   density, and CTA placement.
4. **Mood in one word?** e.g. "ấm và dễ thương" vs "sắc và cao cấp". This
   disambiguates everything else.

If the user named a reference brand, that brand *is* the spec — match its tokens.

## Step 2 — Load the matching token reference

Read the reference file for the chosen direction. Each one contains real,
copy-pasteable design tokens (hex values, font stacks, shadow scales, radius
scales, spacing, component recipes):

- Korean → `references/korean.md`
- International/Western → `references/international.md`
- Implementation details that apply to both (fonts to import, Tailwind/CSS
  setup, shadow + radius systems, dark mode) → `references/implementation.md`

Read only what's relevant. For a Korean fintech app screen, read `korean.md`
plus `implementation.md`; skip `international.md`.

## Step 3 — Build

Use the `frontend-design` skill for the actual build (it handles the
environment's component/styling constraints). Apply the tokens from the
reference file as the visual layer on top. Key reminders:

- **Commit fully to the chosen grammar.** Half-Korean, half-generic reads worse
  than either done cleanly. If you picked Toss-style, every radius, shadow, and
  spacing value should be Toss-scale.
- **One accent color leads.** Both cultures use a single dominant accent that
  carries the brand and guides the eye. Avoid rainbow palettes.
- **Match density to culture.** Korean = generous whitespace, large radii, soft
  diffuse shadows. Western SaaS = tighter, sharper, border-led or near-flat.
- **Type carries the most signal.** The font choice alone (Pretendard vs Inter
  vs SF-style) makes a page read Korean vs Western before any color does. Get
  the font stack right first (see `implementation.md`).

## The two grammars at a glance

This table is a quick mental anchor; the reference files have the exact tokens.

| Axis            | Korean (gu Hàn)                      | Western SaaS            | Western editorial      |
|-----------------|--------------------------------------|-------------------------|------------------------|
| Mood            | warm, friendly, soft                 | sharp, technical        | calm, premium          |
| Accent          | one bold (Toss blue, mint)           | one saturated (indigo)  | mostly mono + 1 accent |
| Radius          | large (16–24px)                      | small–med (6–10px)      | medium (8–16px)        |
| Shadow          | soft, diffuse, low-opacity           | subtle or none; borders | minimal                |
| Type            | Pretendard, bold display, tight      | Inter/Geist, medium     | large SF-style / serif |
| Layout          | mobile-first, stacked cards          | grid, dense, dark-ok    | spacious, full-bleed   |
| Whitespace      | generous                             | efficient               | very generous          |
| Reference       | Toss, Kakao, 29CM, Musinsa           | Linear, Stripe, Vercel  | Apple                  |

## What "looking authentic" actually requires

A page reads as genuinely Korean (not "Western page with mint buttons") when
*several* signals stack: the Pretendard font + oversized border-radius + soft
diffuse shadow + generous vertical rhythm + single bold accent + friendly
personal microcopy, all together. Any one alone isn't enough. The reference
files spell out the full stack for each direction — apply them as a set, not
à la carte.