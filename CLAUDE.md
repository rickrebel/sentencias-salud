# sentencias-salud

Platform and replicable methodology to collect, pre-select (AI + human review) and code Mexican *amparo indirecto* rulings on the right to health (2011–2026), for the academic research led by María Fernanda Cobo Armijo (Ibero CDMX). Ricardo provides the technology and co-designs the methodology; the repo is public because the method itself is a deliverable.

## Status

No code yet — only process documentation. Next session bootstraps the monorepo (`api/` Django + `nuxt/`), starting from the shared "collections" engine (schema-driven catalogs/CRUD used in onigies and ocsa), not from scratch.

## Repos & docs

- Public repo: `rickrebel/sentencias-salud`. `docs/` is a **private** git submodule (`rickrebel/sentencias-salud-docs`): process docs hold transcripts and budgets. Without access, `docs/` is empty and everything else still works.
- `docs/` is a documenter root (`docs/.documenter.json`); validate with `node ~/.claude/skills/documenter/scripts/validate.mjs` run **from `docs/`** (from the repo root it sees 0 documents).
- Two commits per docs change: one inside `docs/`, one in the root updating the submodule pointer.
- Themes are `reference` docs; tasks are flat and closable and point to their theme via `related`.

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
