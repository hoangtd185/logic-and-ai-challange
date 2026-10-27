---
name: refactor-shared-owner
description: >
  Refactor scattered UI/service logic into one domain "owner" hook that owns shared
  state through an already-global container (form library / query cache / store) and
  hides its API behind intentions. Reach for it when handlers call a mutation or
  service directly in onChange/onClick, when the same data is copied across components
  or wizard steps and re-synced with effects, when a callback is prop-drilled through
  many layers, or when the user says "shared owner" / "owner hook" / "stop calling the
  API in the handler".
---

# Refactor: Shared Owner

## Mindset

You are not "managing state" — you are **naming an owner** for a domain concern and
handing every screen the *same* handle to it.

Two moves define the pattern:

1. **Project, don't copy.** State that must be shared across many call sites should
   live where it is *already global* — the form library (`FormProvider`), the query
   cache (react-query), or a store (zustand). The owner is a thin **projection** over
   that container, never a second copy synced by effects.
2. **Encapsulate the API.** Callers never touch `mutation.mutate()` in a handler. They
   express an intention — `redistribute({ channels })` — and the owner decides what and
   when to fetch.

Return shape is always **`{ state (derived), ...actions }`**: read state, fire
intentions, nothing else leaks.

## The one rule that makes it work

> **The owner must not hold local state (`useReducer`/`useState`) that needs sharing.**

Local state is per-call-site: call the hook in three places → three independent states
→ you're forced to prop-drill a callback or wrap a Context that duplicates the store.
Derive from a global container instead, and N call sites read/write **one** state —
genuinely "call anywhere", zero drilling, zero new provider.

Purely-local UI (an open accordion, a hover) may stay local; it never crosses call sites.

## Litmus test

Call the hook in two unrelated components. Do they see the same state **without** you
adding a provider or passing a prop? Yes → done. If sharing needs a provider/prop, the
owner is still holding local state — fix that first.

## When to reach for it

- A handler calls a mutation/service directly: `onChange={() => mutate()}`.
- The same data is duplicated across components / wizard steps and kept in sync with effects.
- One concern's logic is smeared across a "god" component.
- You're prop-drilling a callback 3+ levels to reach the code that uses it.

## Recipe

1. **Name the owner by domain**: `useBudgetOwner`, `useCartOwner`, `useFiltersOwner`.
2. **Pick the container** (single source of truth): form field(s), a query key, or a
   store slice. No new copy of the data.
3. **Reads = pure derivation** from the container (`useWatch` + `useMemo`). Safe in any component.
4. **Actions = imperative writes** to the container. Take a `Partial` and merge it onto
   current state: `act(partial) → write(compute({ ...read(), ...partial }))`.
5. **API = keyed query, not a mutation.** The key is exactly the inputs the result
   depends on. Change an input → auto-refetch; every mounted owner shares one request (dedup).
6. **Compose owners via callbacks, not imports**: `<Channels onChange={c => redistribute({ channels: c })} />`.
7. **One reactive writer.** If the owner writes the API result back into the container,
   gate that side-effect to a single mounted instance (`reactive: true`) so N mounts
   don't storm the store; guard each write with an equality check.

## Minimal example (RHF + react-query)

```ts
function useBudgetOwner({ reactive = false } = {}) {
  const { getValues, setValue } = useFormContext();

  // 1) reads — projection of the already-global form (same at every call site)
  const dist  = useWatch({ name: "budgetDistribution" }) ?? {};
  const total = useWatch({ name: "totalBudget" });

  // 2) API — keyed query: dedup + auto-refetch when the key changes
  const key = buildKey(getValues);              // e.g. enabledPlatforms + durationDays
  const { data: suggest } = useSuggestQuery(key);

  // 3) action — merge a Partial onto current state, then write. No mutate() here.
  const redistribute = (p) => {
    const keys = computeKeys(p.platforms ?? getValues("platforms"),
                             p.channels  ?? getValues("channels"));
    setValue("budgetDistribution", deriveSplit(keys, dist, suggest?.ratios));
  };

  // 4) single reactive writer materializes the query result into the store
  useEffect(() => {
    if (reactive && suggest) applySplit(suggest, getValues, setValue);
  }, [reactive, suggest]);

  return { budget: { dist, total /* + derived: allocations, pie… */ }, redistribute };
}
```

Call it anywhere — one shared state, no provider, no prop-drilling:

```tsx
// Step 2 (Content) — only fires the intention
const { redistribute } = useBudgetOwner();
<Channels onChange={c => redistribute({ channels: c })} />

// Step 3 (Budget) — reads state + owns the reactive write-back
const { budget } = useBudgetOwner({ reactive: true });
<Pie data={budget.dist} />
```

The trigger for "changing X updates the budget" is now **"X is in the query key"** — not
"someone remembered to call mutate() in this handler".

## Pitfalls

- **`useReducer`/`useState` inside the owner** → local state → breaks "call anywhere".
  This is the mistake to catch first.
- **A new Context provider "to share it"** → you re-invented the global container you
  already had, and now sync it by hand. Use the container directly.
- **Multiple reactive writers** → the owner writes on API success in N mounts → write
  storm / render loops. Exactly one `reactive` writer; guard writes with an equality check.
- **Keeping a mutation as the trigger** → if changing X should refetch, put X in the
  query key; don't call `mutate()` from an event handler.
- **Over-globalizing** → local-only UI state stays local. Share only what genuinely
  crosses call sites.
