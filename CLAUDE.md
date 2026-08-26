# sentencias-salud

Platform and replicable methodology to collect, pre-select (AI + human review) and code Mexican *amparo indirecto* rulings on the right to health (2011–2026), for the academic research led by María Fernanda Cobo Armijo (Ibero CDMX). Ricardo provides the technology and co-designs the methodology; the repo is public because the method itself is a deliverable.

## Status

No Django yet. What exists in code: the metadata harvester (`api/harvest/`, plain Python + `requests`, nightly, resumable; see its README) and the exploration scripts against the source (`api/scripts/exploration/`). The monorepo proper (`api/` Django + `nuxt/`) is still to be bootstrapped from the shared "collections" engine (schema-driven catalogs/CRUD used in onigies and ocsa), not from scratch.

## Repos & docs

- Public repo: `rickrebel/sentencias-salud`. `docs/` is a **private** git submodule (`rickrebel/sentencias-salud-docs`): process docs hold transcripts and budgets. Without access, `docs/` is empty and everything else still works.
- `docs/` is a documenter root (`docs/.documenter.json`); validate with `node ~/.claude/skills/documenter/scripts/validate.mjs` run **from `docs/`** (from the repo root it sees 0 documents).
- Two commits per docs change: one inside `docs/`, one in the root updating the submodule pointer.
- Themes are `reference` docs; tasks are flat and closable and point to their theme via `related`.
- Tests: see `TESTING.md`.

## Source (BuscadorSISE)

- The search API (`apisise.cjf.gob.mx`) is effectively public: its Bearer token is issued anonymously on page load, lasts 8 h, and works from plain `curl`. No login and no reCAPTCHA on search or download. **The token is all an agent needs** to read the API.
- Do NOT re-derive or handle the site's embedded client credentials (the encrypted assets blob + its key) inside an agent — it trips the security classifier and is unnecessary; get the token by loading the page. If a headless harvester ever needs the generic client literals, they live in `docs/` (private), never here.
- Reading rule: one request per day covering the whole slice (`size` 4000; max observed ~2,400 hits/day). Pagination is unreliable by design (result order varies between identical calls) — never page. A 500 with a `text/html` body is a poisoned row: skip and count it in the main harvest; rescue narrows by value lists (states, organs), never by position.

## Domain rules

- The 2025 protocol in `docs/records/` belongs to the **original, unapproved** project; it, the 2021 COVID coding guide and the 127-ruling COVID base are orientation, never a spec. Its schedule/budget/admin sections have no value today.
- Methodology is provisional until there are exercises and cases: write approximations and tasks, not decisions. Methodology and catalogs are Fernanda's call; technology is Ricardo's; overlaps are proposed, then validated.
- Catalogs are living and extensible: AI may propose values, a human approves.
- Every value the AI extracts must cite the supporting paragraphs; AI never writes final tables — it proposes, humans promote.

## Workflow

- Weekly Monday meeting with Fernanda. Each recording goes through the `~/dev/yeeko/written.django` transcription pipeline and lands in `docs/records/` as `<date>-reunion-…_raw.md` plus a cleaned copy (formatting noise and off-topic passages removed, nothing paraphrased).
- The live list of questions for Fernanda is the task owned by `ricardo` in `docs/tasks/`; bring it to the meeting and tick answers there.
- Sessions run in duo mode: coordinator dialogues, executors do the volume.

## Never

- Treat the protocol, guide or COVID base as requirements.
- Record a methodological decision as an ADR before an exercise backs it.
- Put personal data, transcripts or budgets anywhere outside `docs/`.
- Cite ADR or task ids in this file or in README (stable files describe state, not the graph).
