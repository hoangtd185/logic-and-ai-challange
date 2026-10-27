---
name: campaign-v4-refactor
description: >
  Work on the Create Campaign V4 / Campaign Details V4 refactor, or onboard a new ad
  channel (TikTok, Naver Search, Naver GFA, LINE, Yahoo! JAPAN YSA/YDA, Amazon, Shopee).
  Reach for it when touching pages/v4/ad-creation, pages/v4/campaigns/detail,
  components/campaigns/create-campaign-multi, components/campaigns/campaign-detail or
  state/platform-factory; when adding a Platform enum member, a Record<Platform, …> map
  or a new per-channel form/mapper/validator; or when the user says "add a channel",
  "channel contract", "campaign v4 refactor", "thêm channel".
---

# Campaign V4 Refactor

## Mindset

The knowledge lives in `docs/campaign-v4/`, not in this file. This skill is a **loop**:
read the plan, do one phase, write the plan back. A phase that leaves the docs stale is
not finished — the next session will re-derive what you already knew, and re-derive it
wrong.

The refactor has exactly one purpose, and it is measurable:

> Adding a channel means **adding a directory** — not editing a union type, a conditional
> type, a `Record<Platform, …>` map, or a `switch`.

## Start of session — read, in this order

1. `docs/campaign-v4/03-REFACTOR-PLAN.md` — **first, always.** Current phase, open
   questions, decisions log. Do not plan from scratch; the plan exists.
2. `docs/campaign-v4/02-CHANNEL-CONTRACT.md` — the target design you must not drift from.
3. Then, only what the task needs:
   - onboarding a channel → `04-ADD-A-CHANNEL.md` + that channel's row in `05-CHANNEL-MATRIX.md`
   - touching an unfamiliar area → `01-CURRENT-STATE.md`

Also honour the root `CLAUDE.md` (V4 tokens, component placement) and the repo's
`refactor-shared-owner` skill — `useBudgetOwner` is the model every extracted owner hook
should copy.

## Rules while working

- **Never widen the coupling.** If a change adds a `Platform.X` branch, a
  `Record<Platform, …>` entry, or a new `<channel>Audience`-style named field, stop: that
  is the problem, not the fix. Find the registry-driven way, or record a contract gap.
- **Contract gaps go in the plan, not in a workaround.** Had to edit a file outside a
  channel's own directory? Write it in `03-REFACTOR-PLAN.md` § Decisions log and fix
  `02-CHANNEL-CONTRACT.md`.
- **The spec leads the code.** If reality should differ from `02-CHANNEL-CONTRACT.md`,
  edit that doc first, in the same session, with the reason.
- **Move before rewriting.** Phase 2 wraps the four existing factories; a rewrite hidden
  inside a migration is how behaviour gets lost.
- **Phase 4 (form shape) ships alone.** Drafts are persisted in the old shape; combining
  it with anything else makes a restore bug unbisectable.
- **Legacy counts.** `pages/v4/**` is a skin over `create-campaign-multi` and
  `campaign-detail`. Refactoring only the skin cannot reduce the cost of a channel.

## Before claiming a phase is done

```bash
cd linkadz-portal-v2
npx tsc --noEmit
grep -rn "Record<Platform\|Record<SupportedPlatform" src --include="*.ts" --include="*.tsx" | wc -l
grep -rln "Platform\.\(GOOGLE\|META\|KAKAO\|LINKEDIN\)" src --include="*.ts" --include="*.tsx" | wc -l
```

Both counts must move **down**, or the phase must explain in the plan why not.
Baseline at 2026-08-31, **after the Phase 0.5 purge**: **30** maps across 17 files,
**158** files. (Pre-purge it read 40 / 20 / 201; the difference was unreachable code, so
treat 30 / 158 as the real starting line.)

## End of session — write back

Not optional, and not "if there's time":

- [ ] Tick / mark `[~]` / `[!]` the items you touched in `03-REFACTOR-PLAN.md`
- [ ] Append any decision to its log — never rewrite an old entry, supersede it
- [ ] Add or resolve entries in § Open questions
- [ ] Contract changed? Edit `02-CHANNEL-CONTRACT.md`
- [ ] Layer deleted or counts moved materially? Update `01-CURRENT-STATE.md`
- [ ] Channel intel arrived (API docs, partner answer)? Update `05-CHANNEL-MATRIX.md`
- [ ] A checklist step in `04-ADD-A-CHANNEL.md` is now obsolete? **Delete it** — that file
      getting shorter is how the refactor reports progress

Then tell the user, in one line, what the counts moved from and to.
