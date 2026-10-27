# Korean aesthetic — "gu Hàn" tokens

The Korean web/app look (Toss, Kakao, Naver, Baemin, 29CM, Musinsa) reads as
*minimal but warm*: clean and uncluttered, yet friendly and approachable rather
than cold. The hallmark moves are oversized rounded corners, soft diffuse
shadows, generous whitespace, a single bold accent, and the Pretendard font.

## Font — the #1 signal

Korean pages overwhelmingly use **Pretendard** (a free, modern sans that covers
Latin + Hangul beautifully). Using it is the fastest way to read "Korean".

```css
font-family: "Pretendard Variable", Pretendard, -apple-system,
  "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif;
```

Import (see `implementation.md` for the CDN line). Fallbacks: Apple SD Gothic
Neo (iOS native), Noto Sans KR. Display headings are **bold (700) with tight
letter-spacing (-0.02em to -0.03em)**; body is 400–500. Korean UI tends to use
heavier display weights than Western SaaS.

## Two sub-variants

### Fintech / utility (Toss, KakaoPay, Baemin, Naver Pay)
Flat-ish, content-first, one strong accent, big numbers, sticky bottom CTA.
This is the cleanest, most iconic Korean look.

### Lifestyle / commerce (29CM, Musinsa, Oliveyoung, W Concept)
More editorial: large product imagery, generous margins, refined thin type for
labels, muted neutral palette letting photos lead. Less "cute", more "premium
boutique". Still big radii and soft shadows, but image-driven.

## Color

Korean palettes are restrained: lots of white/off-white, one dominant accent,
muted secondary tints used sparingly as soft fills behind icons or cards.

**Neutrals (Toss-grade grayscale — excellent default):**
```
--gray-900: #191F28   /* primary text */
--gray-800: #333D4B
--gray-700: #4E5968   /* secondary text */
--gray-600: #6B7684
--gray-500: #8B95A1   /* tertiary / hints */
--gray-400: #B0B8C1
--gray-300: #D1D6DB   /* borders */
--gray-200: #E5E8EB
--gray-100: #F2F4F6   /* page background */
--gray-50:  #F9FAFB
--white:    #FFFFFF   /* card surfaces */
```

**Accent options (pick ONE to lead):**
```
Toss blue:   #3182F6   (the canonical Korean fintech accent)
Mint/teal:   #1D9E75   /* soft, friendly — used in earlier sample */
Kakao yellow:#FEE500   (loud, brand-specific; use carefully)
Coral:       #FF6B6B
Soft purple: #7C5CFC
```

**Pastel soft-fills (behind icons, badges, quick-action chips) — very Korean:**
```
mint:  #E1F5EE   blue:  #E8F3FF   pink:  #FBEAF0
amber: #FFF4E0   purple:#F0EDFE   gray:  #F2F4F6
```
Use the matching dark shade of the same family for text/icon on these fills,
never pure black.

## Radius — go large

Korean UI uses noticeably bigger corners than Western design.
```
--radius-sm:   8px    /* small chips, tags */
--radius-md:  12px    /* buttons, inputs */
--radius-lg:  16px    /* cards */
--radius-xl:  20px    /* feature cards, balance cards */
--radius-2xl: 24px    /* hero cards, bottom sheets */
--radius-full: 9999px /* pills, avatars, icon circles */
```

## Shadow — soft and diffuse

This is what separates a real Korean look from a flat clone. Low opacity, large
blur, minimal/no vertical offset. Never harsh.
```
--shadow-xs: 0 1px 2px rgba(0,0,0,0.04);
--shadow-sm: 0 2px 8px rgba(0,0,0,0.06);
--shadow-md: 0 4px 16px rgba(0,0,0,0.08);
--shadow-lg: 0 8px 24px rgba(0,0,0,0.08);
--shadow-card: 0 6px 20px rgba(0,0,0,0.05);
```
Cards float gently on a `#F9FAFB`/`#F2F4F6` page background with `--shadow-card`.

## Spacing — generous vertical rhythm

Mobile-first single column. Sections breathe. Typical scale (8px base):
```
4, 8, 12, 16, 20, 24, 32, 40, 48, 64
```
Card padding ~20–24px. Section gaps ~24–32px. Don't crowd.

## Layout patterns

- **Mobile-first, single column, vertically stacked cards.** Scroll-as-story.
- **Personal warm greeting header** ("좋은 아침이에요, ○○님 ☀️") — friendly
  microcopy is a defining Korean trait. Avoid dry corporate tone.
- **Hero / balance card**: large number, tight letter-spacing, soft tint fill,
  big radius (20–24px).
- **Quick actions**: row of 4 rounded-square or circular icon chips, each with a
  different soft pastel fill + a short label under it.
- **List rows**: 40–44px leading icon chip (tinted), title + muted timestamp,
  trailing value. Right-aligned amounts; positive in accent/green.
- **Sticky bottom CTA**: full-width, radius-md/lg, bold accent fill, fixed to
  bottom on app screens.
- **Bottom sheets / toggles** with big radii for actions.

## Microcopy

Friendly, personal, slightly playful. Emoji used sparingly and tastefully (☀️
🎉 💛). Honorific "님" after names. Encouraging tone ("이번 달 +12.4% 👍").
For Vietnamese-targeted Korean-style products, mirror the *warmth*, not
literal Korean text.

## Quick recipe — Korean fintech app screen

1. Page bg `#F9FAFB`. Pretendard font.
2. Warm personal greeting header + a small bell/notification circle.
3. Hero balance card: soft mint/blue tint fill, radius 24px, big bold number
   (tight letter-spacing), a small rounded delta badge.
4. 4 quick-action icon chips, each a different pastel fill, label beneath.
5. "Recent" section: list rows with tinted icon chips, muted timestamps,
   right-aligned amounts.
6. Sticky bottom accent CTA, radius 12–16px.
7. Apply `--shadow-card` to floating cards. Generous spacing throughout.

## Anti-patterns (kills the Korean feel)

- Small/sharp corners (≤6px) — instantly reads Western.
- Hard high-contrast shadows or harsh borders everywhere.
- Cramped spacing / dense data tables.
- Multiple competing accent colors.
- Cold, corporate, impersonal copy.
- A generic system font instead of Pretendard.