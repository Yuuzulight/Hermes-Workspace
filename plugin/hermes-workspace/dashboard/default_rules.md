# Vault capture rules

Used when a vault has no agent_rules.md of its own.

## Retrieve

- Search the vault before answering from memory.
- Check a note's `## History` section for anything newer than its prose.
- State uncertainty rather than guessing.

## Capture

- Current state lives in a note's prose. Every change is a dated line appended
  under that note's `## History` section:
  `- **YYYY-MM-DD** — <one or two sentences>.`
  with an optional trailing `*(supersedes: "<old claim>")*` when it replaces an
  earlier claim.
- Route by subject: one note per person (`People/<Name>.md`), per project or
  ongoing area (`Areas/<Name>.md`), cross-cutting topic (`Topics/<Name>.md`),
  stable facts about the vault owner (`Profile.md`).
- Anything dated and relevant beyond one note also gets a one-line entry in
  `Timeline/<year>.md` (reverse-chronological).
- Never write secrets. Never resolve conflicting notes silently.
