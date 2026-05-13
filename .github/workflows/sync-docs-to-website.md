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

engine:
  id: copilot
  model: gpt-4o
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
- IF the docs file you would update does not exist yet, STOP. Add a note in the PR body that a human should bootstrap the page.

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

Use the `github` toolset to read the file tree of `antedotee/krkn-website` under `content/en/docs/krknctl/`. The relevant file is usually named after the command:

- `krknctl run` → `content/en/docs/krknctl/run.md`
- `krknctl list` → `content/en/docs/krknctl/list.md`
- Global flags → `content/en/docs/krknctl/_index.md`

Before editing, ALSO read `antedotee/krkn-website/CLAUDE.md` to learn the project's conventions (heading depth, code-block style, callout shortcodes).

If no matching docs file exists, note this and create the PR anyway — but only with a `Notes for reviewers` entry asking a human to bootstrap that page. Do not invent a new page.

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
