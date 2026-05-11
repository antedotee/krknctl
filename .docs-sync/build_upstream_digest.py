"""Build .docs-sync-digest/ for the krknctl repo.

krknctl is a Go cobra CLI. The user-facing surface is the subcommand
tree (`krknctl --help` → `krknctl <subcmd> --help` → flags). Rather
than parse Go AST (brittle, requires a custom Go program), we extract
the same surface the user sees: shell out to the binary and parse the
help output.

CI builds the binary first; this script walks it.

Output:
  - llms.txt       — index (one line per subcommand)
  - llms-full.txt  — full per-subcommand detail with flag tables
  - digest.sha     — sha256 of all cmd/*.go for cache invalidation
                     (NOT the binary — binary hash drifts on every build
                     even with identical source due to embedded build metadata)

Format mirrors krkn-hub's so the website-side parser is shared. Each
subcommand becomes a `## scenario:` block with `scenario_type: cli_command`
and one row per flag in the parameter table.

Run from the krknctl repo root after building the binary:
    go build -o /tmp/krknctl .
    python3 .docs-sync/build_upstream_digest.py --binary /tmp/krknctl
"""
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


# Strip the version-update banner that cobra emits before help text on
# every invocation. Matched by its distinctive emoji marker.
_BANNER_RE = re.compile(r"^📣.*$", re.MULTILINE)


# Matches a flag line in cobra's help output. Several shapes possible:
#   `      --flag-name string     description text`
#   `      --flag-name            description text`     (bool — no type slot)
#   `  -h, --help                 help for foo`         (short + long)
#   `  -v, --version              version for krknctl`
#
# The type slot, when present, is a single bareword in lowercase between
# the long flag and the description.
_FLAG_LINE_RE = re.compile(
    r"""
    ^\s+                                    # leading indent
    (?:-(?P<short>[a-zA-Z]),\s+)?           # optional short flag (-h)
    --(?P<long>[a-zA-Z][a-zA-Z0-9-]*)       # long flag (--foo-bar)
    (?:\s+(?P<type>[a-z]+(?:\[\])?))?       # optional type (string, int, []string, etc.)
    \s{2,}                                  # 2+ spaces separating flag from description
    (?P<desc>.*?)\s*$                       # description (greedy to end)
    """,
    re.VERBOSE,
)


# Detects section headers in cobra help output.
_FLAGS_SECTION = "Flags:"
_GLOBAL_FLAGS_SECTION = "Global Flags:"
_AVAILABLE_COMMANDS_SECTION = "Available Commands:"


def _clean_help_output(text: str) -> str:
    """Strip the version-update banner and trailing whitespace."""
    return _BANNER_RE.sub("", text).strip()


def _invoke_help(binary: Path, *args: str) -> str:
    """Run `binary [args...] --help` and return cleaned stdout+stderr."""
    cmd = [str(binary), *args, "--help"]
    # cobra writes some banners to stderr; the version-update marker is in
    # stdout. Combine streams for parsing.
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False,
    )
    combined = result.stdout + "\n" + result.stderr
    return _clean_help_output(combined)


def parse_subcommand_list(root_help: str) -> list[str]:
    """Extract the names of subcommands from a help output's
    `Available Commands:` section. Returns command names in source order.

    Skips the always-present `help` and `completion` cobra-builtin entries
    since they don't represent user-facing chaos surface.
    """
    lines = root_help.splitlines()
    in_section = False
    commands: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped == _AVAILABLE_COMMANDS_SECTION:
                in_section = True
            continue
        # Section ends at a blank line OR at the next bare section header
        if not stripped or stripped.endswith(":"):
            break
        # Lines in this section look like `  name           description`
        parts = stripped.split(None, 1)
        if not parts:
            continue
        name = parts[0]
        if name in ("help", "completion"):
            continue
        commands.append(name)
    return commands


def parse_flags(help_text: str) -> list[dict]:
    """Parse the `Flags:` section of `<binary> <subcmd> --help`.

    Skips `Global Flags:` — those are inherited from root and would
    duplicate across every entity. Skips `-h, --help` (cobra-internal).
    """
    lines = help_text.splitlines()
    in_local = False
    flags: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if stripped == _FLAGS_SECTION:
            in_local = True
            continue
        if stripped == _GLOBAL_FLAGS_SECTION:
            break  # global flags are inherited; skip
        if not in_local:
            continue
        if not stripped:
            # Blank line within Flags: section signals end (or it ends
            # at Global Flags: which we already handle above).
            if flags:
                break
            continue

        match = _FLAG_LINE_RE.match(line)
        if not match:
            continue
        long_flag = match.group("long")
        if long_flag == "help":
            continue  # cobra-internal; not user-facing surface

        type_str = match.group("type") or "bool"  # no type slot → boolean flag
        description = (match.group("desc") or "").strip()

        flags.append({
            "name": long_flag,                              # e.g. "kubeconfig"
            "variable": long_flag.replace("-", "_").upper(), # e.g. "KUBECONFIG"
            "type": type_str,
            "default": "",
            "required": False,
            "description": description,
        })
    return flags


def _extract_short_description(help_text: str) -> str:
    """Get the first non-empty line of help — cobra's `Short:` field."""
    for line in help_text.splitlines():
        s = line.strip()
        if s and not s.startswith("Usage:") and not s.startswith("Error:"):
            return s
    return ""


def discover_entities(binary: Path) -> list[dict]:
    """Walk the subcommand tree starting from root; return one entity per
    leaf or branch command.

    Branch commands (those with `Available Commands:`) become their own
    entity AND their children are walked recursively as `parent_child`
    composite names — this preserves disambiguation when two subtrees
    share a leaf name (e.g. `list available` vs `random available`).
    """
    entities: list[dict] = []

    def walk(cmd_path: list[str]) -> None:
        help_text = _invoke_help(binary, *cmd_path)
        name = "_".join(cmd_path) if cmd_path else "root"
        # Root entity is the binary itself; we skip its parameters because
        # they're global flags (we already exclude those from leaf parsing).
        if cmd_path:
            entities.append({
                "name": name,
                "description": _extract_short_description(help_text),
                "flags": parse_flags(help_text),
            })
        # Recurse if this command has its own subcommand list.
        for child in parse_subcommand_list(help_text):
            walk(cmd_path + [child])

    walk([])
    return entities


def render_llms_txt(entities: list[dict], repo_name: str) -> str:
    lines = [
        f"# {repo_name}",
        "",
        "> Auto-generated by .docs-sync/build_upstream_digest.py.",
        "> Do not edit by hand. Used by the docs-sync agent to detect upstream changes.",
        "",
        "## CLI subcommands",
        "",
    ]
    for e in sorted(entities, key=lambda e: e["name"]):
        f = len(e.get("flags", []))
        lines.append(f"- {e['name']} ({f} flag{'s' if f != 1 else ''})")
    return "\n".join(lines) + "\n"


def render_llms_full_txt(entities: list[dict], repo_name: str) -> str:
    """Per-entity detail file. Reuses krkn-hub section format for the
    shared website-side parser; each subcommand is one entity."""
    lines = [
        f"# {repo_name} — full entity details",
        "",
        "> Auto-generated. Source of truth for the krknctl CLI surface.",
        "",
    ]
    for e in sorted(entities, key=lambda e: e["name"]):
        lines.append(f"## scenario: {e['name']}")
        lines.append("scenario_type: cli_command")
        if e.get("description"):
            lines.append(f"description: {e['description']}")
        lines.append("")

        flags = e.get("flags", [])
        if flags:
            lines.append("### parameters")
            lines.append("")
            lines.append("| name | variable | type | default | required | description |")
            lines.append("| ---- | -------- | ---- | ------- | -------- | ----------- |")

            def cell(v):
                return str(v).replace("|", "\\|").replace("\n", " ")

            for f in flags:
                lines.append(
                    f"| {cell(f['name'])} "
                    f"| {cell(f['variable'])} "
                    f"| {cell(f['type'])} "
                    f"| {cell(f['default'])} "
                    f"| {str(f['required']).lower()} "
                    f"| {cell(f['description'])} |"
                )
            lines.append("")
        else:
            lines.append("(no documented flags)")
            lines.append("")
    return "\n".join(lines) + "\n"


def compute_digest_sha(repo_root: Path) -> str:
    """sha256 over cmd/*.go (the cobra source files we ultimately derive
    the help text from). NOT the binary — binary bytes drift run-to-run
    with embedded build timestamps."""
    h = hashlib.sha256()
    cmd_dir = repo_root / "cmd"
    if cmd_dir.is_dir():
        for path in sorted(cmd_dir.glob("*.go")):
            # Skip test files — they don't affect the CLI surface.
            if path.name.endswith("_test.go"):
                continue
            h.update(path.name.encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def build_upstream_digest(
    repo_root: Path,
    output_dir: Path,
    repo_name: str,
    binary: Path,
) -> dict:
    entities = discover_entities(binary)
    output_dir.mkdir(parents=True, exist_ok=True)

    llms = render_llms_txt(entities, repo_name)
    full = render_llms_full_txt(entities, repo_name)
    sha = compute_digest_sha(repo_root)

    (output_dir / "llms.txt").write_text(llms, encoding="utf-8")
    (output_dir / "llms-full.txt").write_text(full, encoding="utf-8")
    (output_dir / "digest.sha").write_text(sha + "\n", encoding="utf-8")

    return {"entity_count": len(entities), "digest_sha": sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path(".docs-sync-digest"))
    parser.add_argument("--repo-name", default="krknctl")
    parser.add_argument(
        "--binary", type=Path, required=True,
        help="Path to the built krknctl binary (cobra help-walking source)",
    )
    args = parser.parse_args(argv)

    if not args.repo_root.is_dir():
        print(f"error: repo root not found: {args.repo_root}", file=sys.stderr)
        return 2
    if not args.binary.is_file():
        print(f"error: binary not found: {args.binary}", file=sys.stderr)
        return 2

    result = build_upstream_digest(
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        repo_name=args.repo_name,
        binary=args.binary,
    )
    print(
        f"Wrote krknctl digest: {result['entity_count']} CLI entities, "
        f"sha={result['digest_sha'][:8]}..."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
