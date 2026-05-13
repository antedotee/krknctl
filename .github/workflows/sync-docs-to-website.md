---
name: Sync krknctl docs to website
description: When a PR merges to main, propose docs updates in the website repo
on:
  pull_request:
    types: [closed]
    branches: [main]

if: github.event.pull_request.merged == true

permissions:
  contents: read
  pull-requests: read

engine: copilot
strict: true

network:
  allowed:
    - defaults
    - github

tools:
  github:
    toolsets: [default]
  edit:
  bash:
    - "git diff *"
    - "git log *"
    - "git show *"
    - "find . -name '*.go' -o -name '*.md'"
    - "cat *"
    - "grep *"

timeout-minutes: 20

safe-outputs:
  github-token: ${{ secrets.GH_AW_CROSS_REPO_PAT }}
  create-pull-request:
    target-repo: antedotee/krkn-website
    title-prefix: "[docs-sync] "
    labels: [automated-docs, source/krknctl]
    draft: false
---

# Sync krknctl docs to website

A PR just merged to `${{ github.repository }}`. Decide if it changes any user-facing CLI flag, command, or config field. If yes, propose corresponding docs updates in `antedotee/krkn-website`.

## Triggering PR

- **Number:** #${{ github.event.pull_request.number }}
- **Title:** ${{ github.event.pull_request.title }}
- **Head SHA:** ${{ github.event.pull_request.head.sha }}
- **URL:** Construct it as `https://github.com/${{ github.repository }}/pull/${{ github.event.pull_request.number }}` when you need to reference it.

## Rules — read first, follow strictly

- DO NOT touch docs sections unrelated to this PR.
- DO NOT invent flags, options, or commands that are not in the diff. Document only what the code actually adds, renames, or removes.
- DO NOT modify the docs repo's `CLAUDE.md`, `hugo.yaml`, `layouts/`, `assets/`, or any file under `static/`.
- DO NOT include narrative about *why* the change was made — describe only the resulting user-facing behavior.
- DO NOT hardcode hex colors or styles; if the docs file uses CSS custom properties, preserve them.
- IF the merged PR makes no user-facing CLI flag, command, or config-field change, STOP. Do not create a PR.
- IF no command-specific docs file matches, FALL BACK to `content/en/docs/krknctl/usage.md`. Never STOP just because a guessed-at file path is missing — always pick the closest existing file. Use `_index.md` as last-resort fallback.

## Steps

### 1. Read the merged PR

Use the `github` toolset (`pull_request_read`, `get_commit`, `list_files`) to fetch PR #${{ github.event.pull_request.number }} on `${{ github.repository }}`. Get the list of changed files and their diffs.

### 2. Find user-facing surface changes in the diff

Scan changed files in priority order:

- `cmd/**/*.go` — cobra command definitions. Look for `cobra.Command{ ... Use, Short, Flags() }` and any new `.Flags().StringVar`, `.BoolVar`, `.IntVar`, etc.
- `internal/config/`, `pkg/config/` — config struct tags (`json:"..."`, `yaml:"..."`, `mapstructure:"..."`).
- Anywhere `viper.BindPFlag`, `viper.SetDefault`, or `pflag` is called.
- `schemas/**` — JSON/YAML schemas.

For each detected change, write a one-line summary. Examples:

- `Added flag --namespace to krknctl run`
- `Renamed config key chaos.duration → chaos.duration_seconds`
- `Removed deprecated flag --legacy-mode`
- `Changed default value of --retries from 3 to 5`

If the list is empty after scanning every changed file: **STOP. Do not call create_pull_request. Exit silently.**

### 3. Locate the docs file to edit

Use the `github` toolset to **list the actual files** under `antedotee/krkn-website/content/en/docs/krknctl/` (do NOT assume which files exist). At time of writing, the real files are:

- `content/en/docs/krknctl/usage.md` — documents `krknctl run` and all its flags. **This is the default target for any flag/option/config change to the `run` command.**
- `content/en/docs/krknctl/_index.md` — landing page for krknctl docs.
- `content/en/docs/krknctl/randomized-chaos-testing.md` — randomized testing mode.

Decision rule:
1. If a file's name matches the changed command (e.g. `randomized-chaos-testing.md` for changes to randomized mode), use it.
2. Otherwise, for changes to `krknctl run` (flags, options, config), update **`usage.md`** — this is the standing reference for `run`.
3. Last-resort fallback: `_index.md`.

NEVER refuse to act because a file like `run.md` doesn't exist — that's expected. The correct file for `run` is `usage.md`.

Before editing, ALSO read `antedotee/krkn-website/CLAUDE.md` to learn the project's conventions (heading depth, code-block style, callout shortcodes).

If you genuinely cannot find ANY reasonable file (extremely rare — `usage.md` always exists for `run` changes), use `_index.md` and add a `Notes for reviewers` entry suggesting a dedicated page. Always prefer creating the PR over calling `noop`.

### 4. Make the smallest possible edit

Use the `edit` tool. Touch only sections affected by the diff. Match existing tone, heading depth, and parameter-table format exactly.

For a new flag, locate the existing parameter table (usually a markdown table or definition list) and add one row. Do not reformat the rest of the table.

### 5. Open the cross-repo PR

Call the `create_pull_request` MCP tool from the safe-outputs server (NOT the GitHub MCP server's `create_pull_request`). The PR body MUST be exactly this template, filled in:

```markdown
## Auto-generated docs update

**Triggered by:** https://github.com/${{ github.repository }}/pull/${{ github.event.pull_request.number }}
**Source commit:** ${{ github.event.pull_request.head.sha }}

### Detected user-facing changes
- <one bullet per change from Step 2>

### Files modified
- <one bullet per docs file edited, with a one-line description of what changed>

### Notes for reviewers
<anything skipped, ambiguous, or that needs human attention. Write "None." if nothing.>

---
*Refine this PR by commenting `@krkn-docs-sync <your feedback>` on it. The refinement bot will push a follow-up commit to this branch.*
```
