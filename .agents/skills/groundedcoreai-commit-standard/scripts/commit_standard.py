#!/usr/bin/env python3
"""Deterministic helper for the groundedcoreai-commit-standard skill.

Adapted from the Deepexi git-commit-standard skill: TAPD references are
replaced by GitHub issue references, the HMAC key can be supplied via the
GCA_COMMIT_HMAC_KEY environment variable, the commit-msg hook is opt-in
(--enforce), and a Co-Authored-By attribution trailer is appended after
signing.

The built-in fallback HMAC key is intentionally local and readable. It is
not a security boundary; it raises the cost of casual hand-written messages
while keeping the workflow offline and easy to distribute. Set
GCA_COMMIT_HMAC_KEY to a team-specific value for real deployments.
"""

from __future__ import print_function

import argparse
import hashlib
import hmac
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


VERSION = "1.0.0"
SUPPORTED_VERSIONS = ("1.0.0",)
POLICY = "groundedcore-conventional-v1"
SKILL_NAME = "groundedcoreai-commit-standard"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DEFAULT_HMAC_KEY = b"groundedcore-commit-standard-default-key-v1-not-a-security-boundary"
HMAC_KEY_ENV = "GCA_COMMIT_HMAC_KEY"
BODY_LINE_MAX = 120
INSTRUCTION_KEYWORD = "groundedcoreai-commit-standard"
INSTRUCTION_SECTION = (
    "## Git Commit Standard\n\n"
    "提交代码、执行 `git commit` 时一律需要使用 "
    "`groundedcoreai-commit-standard` skill。\n"
    "不要手写普通 commit message；如需提交请通过该 skill 生成、签名并执行提交。\n"
)
ATTRIBUTION = "Co-Authored-By: Claude Code <noreply@anthropic.com>"

ALLOWED_AGENTS = ("codex", "claude", "cursor", "manual-ai")
ALLOWED_TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
# Strict types must use the four-paragraph body (affected interfaces, why,
# core changes, risk/compatibility). Other types only need a concrete body.
STRICT_TYPES = ("feat", "fix", "perf", "refactor")

TRAILER_KEYS = [
    "GroundedCore-Commit-Skill",
    "GroundedCore-Commit-Skill-Version",
    "GroundedCore-Commit-Issue-URL",
    "GroundedCore-Commit-Tree",
    "GroundedCore-Commit-Parents",
    "GroundedCore-Commit-Diff-Base",
    "GroundedCore-Commit-Diff-SHA256",
]
SIGNATURE_KEY = "GroundedCore-Commit-Signature"
ALL_TRAILER_KEYS = TRAILER_KEYS + [SIGNATURE_KEY]

SUBJECT_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"\((?P<scope>[a-z0-9][a-z0-9._-]{0,29})\): "
    r"(?P<issue>--issue=(?P<num>\d{1,7})|--no-issue) "
    r"(?P<summary>.+)$"
)
EMAIL_RE = re.compile(r"<([^<>]+)>")
BULLET_RE = re.compile(r"^(\s*[-*+]\s+|\s*\d+[.)]\s+)")
AFFECTED_API_RE = re.compile(
    r"^(?:影响接口|Affected APIs?)[:：]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]):/(.*)$")
GITHUB_REMOTE_RES = (
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
)
VAGUE_SUMMARIES = (
    "修改问题",
    "修复bug",
    "代码优化",
    "update code",
    "fix bug",
    "misc changes",
)


class GroundedCoreError(Exception):
    pass


def die(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def run_command(args, check=True, text=True):
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if text:
        kwargs["universal_newlines"] = True
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    proc = subprocess.run(args, **kwargs)
    if check and proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise GroundedCoreError("%s failed: %s" % (" ".join(args), stderr.strip()))
    return proc


def git(args, check=True):
    return run_command(["git"] + list(args), check=check, text=True)


def git_bytes(args, check=True):
    return run_command(["git"] + list(args), check=check, text=False)


def git_stdout(args, check=True):
    return git(args, check=check).stdout.strip()


def ensure_git_repo():
    proc = git(["rev-parse", "--is-inside-work-tree"], check=False)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise GroundedCoreError("Not inside a Git work tree.")


def repo_root():
    ensure_git_repo()
    return Path(git_stdout(["rev-parse", "--show-toplevel"]))


def git_dir():
    ensure_git_repo()
    return Path(git_stdout(["rev-parse", "--absolute-git-dir"]))


def git_config_get(key, local=False, path=False):
    args = ["config"]
    if local:
        args.append("--local")
    if path:
        args.append("--path")
    args.extend(["--get", key])
    proc = git(args, check=False)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value if value else None


def git_config_bool(key):
    proc = git(["config", "--bool", "--get", key], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().lower()


def git_config_set(key, value):
    git(["config", "--local", key, value])


def git_config_unset(key):
    git(["config", "--local", "--unset", key], check=False)


def hmac_key():
    value = os.environ.get(HMAC_KEY_ENV)
    if value:
        return value.encode("utf-8")
    return DEFAULT_HMAC_KEY


def normalize_lf(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def append_instruction_section(path):
    if path.exists():
        text = read_text(str(path))
        if INSTRUCTION_KEYWORD.lower() in text.lower():
            return False
        if text and not text.endswith("\n"):
            text += "\n"
        if text and not text.endswith("\n\n"):
            text += "\n"
        text += INSTRUCTION_SECTION
    else:
        text = "# %s\n\n%s" % (path.name, INSTRUCTION_SECTION)
    write_text(str(path), text)
    return True


def ensure_project_instruction_docs():
    root = repo_root()
    agents_doc = root / "AGENTS.md"
    claude_doc = root / "CLAUDE.md"
    changed = []

    if agents_doc.exists():
        if append_instruction_section(agents_doc):
            changed.append("AGENTS.md")
    elif claude_doc.exists():
        if append_instruction_section(claude_doc):
            changed.append("CLAUDE.md")
    else:
        if append_instruction_section(agents_doc):
            changed.append(agents_doc.name)

    if changed:
        print("Updated project instruction docs: %s" % ", ".join(changed), flush=True)
    return changed


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def sh_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def to_git_sh_path(path):
    value = str(path).replace("\\", "/")
    match = WINDOWS_DRIVE_RE.match(value)
    if match:
        return "/" + match.group(1).lower() + "/" + match.group(2)
    return value


def is_absish(path_text):
    return (
        os.path.isabs(path_text)
        or re.match(r"^[A-Za-z]:[\\/]", path_text) is not None
        or re.match(r"^/[A-Za-z]/", path_text) is not None
    )


def resolve_hook_path(path_text, base_dir):
    expanded = os.path.expanduser(path_text)
    if is_absish(expanded):
        return Path(expanded)
    return Path(base_dir) / expanded


def same_file_or_path(left, right):
    left_path = Path(left)
    right_path = Path(right)
    try:
        return left_path.exists() and right_path.exists() and left_path.samefile(right_path)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(str(left_path))) == os.path.normcase(
        os.path.abspath(str(right_path))
    )


def chmod_executable(path):
    try:
        mode = os.stat(str(path)).st_mode
        os.chmod(str(path), mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        # Windows Git Bash usually does not require Unix executable bits.
        pass


def install_runtime():
    source = Path(__file__).resolve()
    target_dir = Path.home() / ".groundedcore" / "commit" / "versions" / VERSION / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "commit_standard.py"

    if not target.exists() or not same_file_or_path(source, target):
        shutil.copy2(str(source), str(target))
    chmod_executable(target)

    current_version = Path.home() / ".groundedcore" / "commit" / "current-version"
    current_version.parent.mkdir(parents=True, exist_ok=True)
    write_text(str(current_version), VERSION + "\n")
    return target


def detect_previous_hook(wrapper_path, force):
    current_hooks_path = git_config_get("core.hooksPath", local=True, path=True)
    root = repo_root()
    gitdir = git_dir()

    if current_hooks_path:
        hooks_dir = resolve_hook_path(current_hooks_path, root)
        candidate = hooks_dir / "commit-msg"
        if same_file_or_path(candidate, wrapper_path):
            return None
        if candidate.exists():
            return candidate
        if force:
            return None
        raise GroundedCoreError(
            "Existing core.hooksPath is configured but commit-msg cannot be "
            "resolved safely: %s" % candidate
        )

    legacy = gitdir / "hooks" / "commit-msg"
    if legacy.exists() and not same_file_or_path(legacy, wrapper_path):
        return legacy
    return None


def write_hook_wrapper(wrapper_path, runtime_path):
    runtime = to_git_sh_path(runtime_path)
    content = """#!/bin/sh

GCA_RUNTIME=%s
PREVIOUS_HOOK="$(git config --path --get groundedcore.commit.previousCommitMsgHook || true)"

if [ -n "$PREVIOUS_HOOK" ]; then
  if [ -x "$PREVIOUS_HOOK" ]; then
    "$PREVIOUS_HOOK" "$1" || exit $?
  elif [ -f "$PREVIOUS_HOOK" ]; then
    sh "$PREVIOUS_HOOK" "$1" || exit $?
  fi
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$GCA_RUNTIME" verify --commit-msg "$1"
elif command -v python >/dev/null 2>&1; then
  exec python "$GCA_RUNTIME" verify --commit-msg "$1"
elif command -v py >/dev/null 2>&1; then
  exec py -3 "$GCA_RUNTIME" verify --commit-msg "$1"
else
  echo "Python 3 is required by groundedcoreai-commit-standard hook." >&2
  exit 1
fi
""" % sh_quote(runtime)
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(str(wrapper_path), content)
    chmod_executable(wrapper_path)


def install_hook(args):
    ensure_git_repo()
    runtime = install_runtime()
    gitdir = git_dir()
    hooks_dir = gitdir / "groundedcore" / "hooks"
    wrapper = hooks_dir / "commit-msg"
    previous = detect_previous_hook(wrapper, getattr(args, "enforce_hook", False))

    write_hook_wrapper(wrapper, runtime)
    git_config_set("core.hooksPath", to_git_sh_path(hooks_dir))
    git_config_set("groundedcore.commit.required", "true")
    git_config_set("groundedcore.commit.policy", POLICY)
    git_config_set("groundedcore.commit.skill", SKILL_NAME)
    git_config_set("groundedcore.commit.runtime", to_git_sh_path(runtime))
    if previous:
        git_config_set("groundedcore.commit.previousCommitMsgHook", to_git_sh_path(previous))
    else:
        git_config_unset("groundedcore.commit.previousCommitMsgHook")

    print("Installed groundedcoreai-commit-standard hook: %s" % to_git_sh_path(wrapper), flush=True)
    print("Runtime: %s" % to_git_sh_path(runtime), flush=True)


def parse_author_email_from_ident(ident):
    match = EMAIL_RE.search(ident)
    if not match:
        raise GroundedCoreError("Unable to parse author email from Git author ident.")
    return match.group(1)


def current_author_email():
    ident = git_stdout(["var", "GIT_AUTHOR_IDENT"])
    return parse_author_email_from_ident(ident)


def current_parents():
    parents = []
    head_proc = git(["rev-parse", "--verify", "HEAD"], check=False)
    if head_proc.returncode == 0:
        parents.append(head_proc.stdout.strip())

    merge_head_path = git_stdout(["rev-parse", "--git-path", "MERGE_HEAD"], check=False)
    if merge_head_path and os.path.exists(merge_head_path):
        with open(merge_head_path, "r", encoding="utf-8") as fh:
            for line in fh:
                value = line.strip()
                if value and value not in parents:
                    parents.append(value)
    return parents


def parents_value(parents):
    return ",".join(parents) if parents else "none"


def diff_base_for_parents(parents):
    return parents[0] if parents else EMPTY_TREE


def staged_context(require_changes=True):
    tree = git_stdout(["write-tree"])
    parents = current_parents()
    diff_base = diff_base_for_parents(parents)
    diff = git_bytes(
        ["diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--no-color", diff_base]
    ).stdout
    if require_changes and not diff:
        raise GroundedCoreError(
            "No staged changes found. Stage files with git add before using "
            "groundedcoreai-commit-standard."
        )
    return {
        "author_email": current_author_email(),
        "tree": tree,
        "parents": parents_value(parents),
        "diff_base": diff_base,
        "diff_sha256": sha256_bytes(diff),
    }


def commit_context(commit):
    raw = git_bytes(["cat-file", "commit", commit]).stdout
    try:
        header_raw, message_raw = raw.split(b"\n\n", 1)
    except ValueError:
        raise GroundedCoreError("Invalid commit object: %s" % commit)

    headers = header_raw.decode("utf-8", "replace").split("\n")
    tree = None
    parents = []
    author_email = None
    for line in headers:
        if line.startswith("tree "):
            tree = line.split(" ", 1)[1]
        elif line.startswith("parent "):
            parents.append(line.split(" ", 1)[1])
        elif line.startswith("author "):
            author_email = parse_author_email_from_ident(line)
    if not tree or not author_email:
        raise GroundedCoreError("Commit object is missing tree or author metadata.")

    message = message_raw.decode("utf-8")
    diff_base = diff_base_for_parents(parents)
    diff = git_bytes(
        ["diff", "--binary", "--full-index", "--no-ext-diff", "--no-color", diff_base, commit]
    ).stdout
    return (
        {
            "author_email": author_email,
            "tree": tree,
            "parents": parents_value(parents),
            "diff_base": diff_base,
            "diff_sha256": sha256_bytes(diff),
        },
        message,
    )


def default_issue_url(issue_id):
    remote = git_config_get("remote.origin.url")
    if not remote:
        return None
    for pattern in GITHUB_REMOTE_RES:
        match = pattern.match(remote)
        if match:
            return "https://github.com/%s/%s/issues/%s" % (
                match.group("owner"),
                match.group("repo"),
                issue_id,
            )
    return None


def validate_issue_args(issue_id, issue_url):
    if issue_id == "none-confirmed":
        if issue_url != "none":
            raise GroundedCoreError(
                "No-issue commits must use --issue-id none-confirmed --issue-url none."
            )
        return "none"

    if not re.match(r"^\d{1,7}$", issue_id or ""):
        raise GroundedCoreError("GitHub issue number must be 1-7 digits, or none-confirmed.")

    url = issue_url
    if url == "none" or not url:
        url = default_issue_url(issue_id)
        if not url:
            raise GroundedCoreError(
                "Unable to derive the issue URL from remote.origin.url; "
                "pass --issue-url explicitly."
            )
    if "/issues/%s" % issue_id not in url:
        raise GroundedCoreError("Issue URL does not match issue number %s." % issue_id)
    return url


def strip_skill_trailer_lines(text):
    text = normalize_lf(text)
    kept = []
    for line in text.split("\n"):
        if line.startswith("GroundedCore-Commit-"):
            continue
        if line.startswith("Co-Authored-By:"):
            continue
        kept.append(line.rstrip())
    while kept and kept[-1] == "":
        kept.pop()
    if not kept:
        raise GroundedCoreError("Commit message is empty.")
    return "\n".join(kept) + "\n"


def trailer_lines(trailers, include_signature=False):
    keys = ALL_TRAILER_KEYS if include_signature else TRAILER_KEYS
    return ["%s: %s" % (key, trailers[key]) for key in keys]


def build_message(base_message, trailers, include_signature=False):
    base = strip_skill_trailer_lines(base_message).rstrip("\n")
    return base + "\n\n" + "\n".join(trailer_lines(trailers, include_signature)) + "\n"


def collect_skill_trailers(text):
    text = normalize_lf(text)
    trailers = {}
    order = []
    for line in text.split("\n"):
        if not line.startswith("GroundedCore-Commit-"):
            continue
        if ": " not in line:
            raise GroundedCoreError("Invalid GroundedCore trailer line: %s" % line)
        key, value = line.split(": ", 1)
        if key in trailers:
            raise GroundedCoreError("Duplicate GroundedCore trailer: %s" % key)
        trailers[key] = value
        order.append(key)
    return trailers, order


def validate_trailer_order(text, require_signature):
    trailers, order = collect_skill_trailers(text)
    expected = ALL_TRAILER_KEYS if require_signature else TRAILER_KEYS
    if order != expected:
        raise GroundedCoreError(
            "GroundedCore trailers must appear exactly once and in the required order."
        )
    return trailers


def canonical_message_without_signature(text):
    text = normalize_lf(text)
    lines = [line.rstrip() for line in text.split("\n")]
    filtered = []
    for line in lines:
        if line.startswith(SIGNATURE_KEY + ": "):
            continue
        # The Co-Authored-By attribution is appended after signing and is not
        # part of the signed payload; exclude it from the canonical form.
        if line.startswith("Co-Authored-By:"):
            continue
        filtered.append(line)
    while filtered and filtered[-1] == "":
        filtered.pop()
    canonical = "\n".join(filtered) + "\n"
    validate_trailer_order(canonical, require_signature=False)
    return canonical


def make_payload(issue_url, context, message_sha256, skill_version=VERSION):
    lines = [
        "skill=%s" % SKILL_NAME,
        "skill_version=%s" % skill_version,
        "issue_url=%s" % issue_url,
        "author_email=%s" % context["author_email"],
        "tree=%s" % context["tree"],
        "parents=%s" % context["parents"],
        "diff_base=%s" % context["diff_base"],
        "diff_sha256=%s" % context["diff_sha256"],
        "message_sha256=%s" % message_sha256,
    ]
    return "\n".join(lines) + "\n"


def hmac_signature(payload):
    return hmac.new(hmac_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_message_text(message, agent, issue_id, issue_url):
    if agent and agent not in ALLOWED_AGENTS:
        raise GroundedCoreError("Unsupported agent: %s" % agent)
    issue_url = validate_issue_args(issue_id, issue_url)
    subject = strip_skill_trailer_lines(message).split("\n", 1)[0].strip()
    subject_issue_id = parse_subject(subject)
    if subject_issue_id != issue_id:
        raise GroundedCoreError("Subject issue reference does not match command issue arguments.")
    context = staged_context(require_changes=True)

    trailers = {
        "GroundedCore-Commit-Skill": SKILL_NAME,
        "GroundedCore-Commit-Skill-Version": VERSION,
        "GroundedCore-Commit-Issue-URL": issue_url,
        "GroundedCore-Commit-Tree": context["tree"],
        "GroundedCore-Commit-Parents": context["parents"],
        "GroundedCore-Commit-Diff-Base": context["diff_base"],
        "GroundedCore-Commit-Diff-SHA256": context["diff_sha256"],
    }
    partial = build_message(message, trailers, include_signature=False)
    validate_policy_message(partial, require_signature=False)
    canonical = canonical_message_without_signature(partial)
    message_sha = sha256_text(canonical)
    payload = make_payload(issue_url, context, message_sha, VERSION)
    trailers[SIGNATURE_KEY] = "hmac-sha256:%s" % hmac_signature(payload)
    full = build_message(message, trailers, include_signature=True)
    validate_policy_message(full, require_signature=True)
    return full.rstrip("\n") + "\n\n" + ATTRIBUTION + "\n"


def parse_subject(subject):
    match = SUBJECT_RE.match(subject)
    if not match:
        raise GroundedCoreError("Subject does not match the GroundedCore Conventional Commit format.")
    summary = match.group("summary")
    if summary.endswith(("。", ".", "！", "!", "？", "?")):
        raise GroundedCoreError("Subject summary must not end with punctuation.")
    if summary.strip().lower() in VAGUE_SUMMARIES:
        raise GroundedCoreError("Subject summary is too vague.")
    if match.group("num"):
        return match.group("num")
    return "none-confirmed"


def message_without_skill_trailers(text):
    text = normalize_lf(text)
    kept = []
    for line in text.split("\n"):
        if line.startswith("GroundedCore-Commit-"):
            continue
        kept.append(line.rstrip())
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept) + "\n"


def body_paragraphs(body):
    paragraphs = []
    current = []
    for line in normalize_lf(body).split("\n"):
        stripped = line.strip()
        if stripped:
            current.append(line.rstrip())
            continue
        if current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def non_meta_paragraphs(body):
    paragraphs = []
    for paragraph in body_paragraphs(body):
        stripped_lines = [line.strip() for line in paragraph if line.strip()]
        if len(stripped_lines) == 1 and (
            stripped_lines[0].startswith("Issue: ")
            or stripped_lines[0].startswith("Co-Authored-By:")
        ):
            continue
        if stripped_lines:
            paragraphs.append(stripped_lines)
    return paragraphs


def validate_body(body, issue_url, strict):
    lines = [line.rstrip() for line in body.split("\n")]
    content = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Issue: "):
            continue
        if stripped.startswith("Co-Authored-By:"):
            continue
        content.append(stripped)

    if not content:
        raise GroundedCoreError(
            "Commit body must describe why, what changed, and risk/compatibility."
        )

    if strict:
        paragraphs = non_meta_paragraphs(body)
        if len(paragraphs) < 4:
            raise GroundedCoreError(
                "Commit body must use blank-line separated paragraphs for "
                "affected interfaces, why, core changes, and risk/compatibility."
            )
        if not AFFECTED_API_RE.match(paragraphs[0][0]):
            raise GroundedCoreError("First body paragraph must start with: 影响接口：...")

    for line in content:
        if len(line) > BODY_LINE_MAX:
            raise GroundedCoreError(
                "Commit body line exceeds %d characters; split it with real line breaks."
                % BODY_LINE_MAX
            )

    bullet_count = sum(1 for line in content if BULLET_RE.match(line))
    if content and BULLET_RE.match(content[0]):
        raise GroundedCoreError("Commit body must use natural paragraphs, not bullet lists.")
    if bullet_count >= 2 and bullet_count * 2 >= len(content):
        raise GroundedCoreError("Commit body must not be primarily a bullet list.")

    if issue_url != "none":
        expected = "Issue: %s" % issue_url
        if expected not in [line.strip() for line in lines]:
            raise GroundedCoreError("Commit body must include original issue URL: %s" % expected)


def validate_policy_message(text, require_signature):
    trailers = validate_trailer_order(text, require_signature=require_signature)

    if trailers["GroundedCore-Commit-Skill"] != SKILL_NAME:
        raise GroundedCoreError("Unsupported GroundedCore commit skill.")
    skill_version = trailers["GroundedCore-Commit-Skill-Version"]
    if skill_version not in SUPPORTED_VERSIONS:
        raise GroundedCoreError(
            "Unsupported groundedcoreai-commit-standard skill version: %s" % skill_version
        )

    no_trailer_message = message_without_skill_trailers(text)
    parts = no_trailer_message.split("\n", 1)
    subject = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""

    subject_match = SUBJECT_RE.match(subject)
    if not subject_match:
        raise GroundedCoreError(
            "Subject does not match the GroundedCore Conventional Commit format."
        )
    commit_type = subject_match.group("type")
    issue_id = parse_subject(subject)

    trailer_issue_url = trailers["GroundedCore-Commit-Issue-URL"]
    resolved_url = validate_issue_args(issue_id, trailer_issue_url)
    if trailer_issue_url != "none" and resolved_url != trailer_issue_url:
        raise GroundedCoreError("Issue URL trailer does not match the subject issue reference.")

    validate_body(body, trailer_issue_url, strict=commit_type in STRICT_TYPES)
    return trailers


def verify_signature(text, context, require_signature=True):
    trailers = validate_policy_message(text, require_signature=require_signature)
    if not require_signature:
        return True

    if trailers["GroundedCore-Commit-Tree"] != context["tree"]:
        raise GroundedCoreError("Tree hash does not match staged/commit content.")
    if trailers["GroundedCore-Commit-Parents"] != context["parents"]:
        raise GroundedCoreError("Parent list does not match current commit context.")
    if trailers["GroundedCore-Commit-Diff-Base"] != context["diff_base"]:
        raise GroundedCoreError("Diff base does not match current commit context.")
    if trailers["GroundedCore-Commit-Diff-SHA256"] != context["diff_sha256"]:
        raise GroundedCoreError("Diff SHA256 does not match current commit context.")

    canonical = canonical_message_without_signature(text)
    message_sha = sha256_text(canonical)
    payload = make_payload(
        trailers["GroundedCore-Commit-Issue-URL"],
        context,
        message_sha,
        trailers["GroundedCore-Commit-Skill-Version"],
    )
    expected = "hmac-sha256:%s" % hmac_signature(payload)
    actual = trailers[SIGNATURE_KEY]
    if not hmac.compare_digest(expected, actual):
        raise GroundedCoreError("GroundedCore commit signature does not match.")
    return True


def command_sign(args):
    ensure_git_repo()
    message = read_text(args.message_file)
    signed = sign_message_text(message, args.agent, args.issue_id, args.issue_url)
    output = args.output_file or args.message_file
    write_text(output, signed)
    print("Signed commit message: %s" % output)


def command_verify(args):
    required = git_config_bool("groundedcore.commit.required")
    if required != "true":
        print("groundedcore.commit.required is not true; skipping GroundedCore commit check.")
        return
    message = read_text(args.commit_msg)
    context = staged_context(require_changes=False)
    verify_signature(message, context, require_signature=True)
    print("GroundedCore commit message verified.")


def command_verify_commit(args):
    ensure_git_repo()
    context, message = commit_context(args.commit)
    verify_signature(message, context, require_signature=True)
    print("GroundedCore commit object verified: %s" % args.commit)


def command_commit(args):
    ensure_git_repo()
    ensure_project_instruction_docs()
    if args.enforce:
        install_hook(args)
    original = read_text(args.message_file)
    signed = sign_message_text(original, args.agent, args.issue_id, args.issue_url)

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as fh:
            fh.write(signed)
            temp_name = fh.name
        proc = git(["commit", "-F", temp_name], check=False)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def build_parser():
    parser = argparse.ArgumentParser(description="groundedcoreai-commit-standard helper")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    install = subparsers.add_parser("install-hook", help="install repository-local commit-msg hook")
    install.add_argument(
        "--enforce-hook",
        action="store_true",
        help="overwrite an unresolvable existing hooks path instead of stopping",
    )
    install.set_defaults(func=install_hook)

    sign = subparsers.add_parser("sign", help="sign a commit message file")
    sign.add_argument("--message-file", required=True)
    sign.add_argument("--output-file")
    sign.add_argument("--agent", choices=ALLOWED_AGENTS, help=argparse.SUPPRESS)
    sign.add_argument("--issue-id", required=True, help="GitHub issue number or none-confirmed")
    sign.add_argument(
        "--issue-url",
        required=True,
        help="full GitHub issue URL, or none to derive from remote.origin.url",
    )
    sign.set_defaults(func=command_sign)

    verify = subparsers.add_parser("verify", help="verify a commit-msg hook message file")
    verify.add_argument("--commit-msg", required=True)
    verify.set_defaults(func=command_verify)

    verify_commit = subparsers.add_parser("verify-commit", help="verify an existing commit object")
    verify_commit.add_argument("--commit", required=True)
    verify_commit.set_defaults(func=command_verify_commit)

    commit = subparsers.add_parser("commit", help="sign message and run git commit")
    commit.add_argument("--message-file", required=True)
    commit.add_argument("--agent", choices=ALLOWED_AGENTS, help=argparse.SUPPRESS)
    commit.add_argument("--issue-id", required=True, help="GitHub issue number or none-confirmed")
    commit.add_argument(
        "--issue-url",
        required=True,
        help="full GitHub issue URL, or none to derive from remote.origin.url",
    )
    commit.add_argument(
        "--enforce",
        action="store_true",
        help="also install the repository-local commit-msg hook so every commit is verified",
    )
    commit.set_defaults(func=command_commit)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except GroundedCoreError as exc:
        die("GroundedCore commit policy check failed.\n\n%s" % exc)


if __name__ == "__main__":
    main()
