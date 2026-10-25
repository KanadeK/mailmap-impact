"""Compare display identities using Git's resolver, without changing source history."""

import hashlib
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
ONE = re.compile(r"([^<>]+)\s*<([^<>\s]+)>\s*")
TWO = re.compile(r"([^<>]*)<([^<>\s]+)>\s*([^<>]*)<([^<>\s]+)>\s*")


class InputError(ValueError):
    pass


def git_env():
    # Environment-supplied repo/config selection must not contaminate either map.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_NO_LAZY_FETCH="1",
        GIT_OPTIONAL_LOCKS="0",
        LC_ALL="C",
    )
    return env


def git(repo, *args, data=None):
    command = [
        "git",
        "--no-pager",
        "--no-replace-objects",
        "-C",
        str(repo),
        "-c",
        "protocol.allow=never",
        "-c",
        "log.showSignature=false",
        "-c",
        "core.fsmonitor=false",
        *args,
    ]
    try:
        result = subprocess.run(command, input=data, capture_output=True, env=git_env(), timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InputError("Git unavailable or command exceeded 60 seconds") from exc
    if result.returncode:
        # No input content is included in our own errors. Native diagnostics can contain paths.
        detail = result.stderr.decode("utf-8", errors="replace")[:2000].strip()
        raise InputError(f"Git failed: {detail}")
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise InputError("Git output exceeds 64 MiB; select a smaller history")
    return result.stdout


def read_map(path):
    if path is None:
        return b""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise InputError("mailmap must be a regular non-symlink file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MAP_BYTES + 1)
    if len(raw) > MAX_MAP_BYTES:
        raise InputError("mailmap exceeds 4 MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InputError("mailmap must be UTF-8") from exc
    if any((ord(c) < 32 and c not in "\t\r\n") or c in "\x85\u2028\u2029" for c in text):
        raise InputError("unsupported control or line separator in mailmap")
    if "\r" in text.replace("\r\n", ""):
        raise InputError("use LF or CRLF line endings in mailmap")
    for number, line in enumerate(text.splitlines(), 1):
        content = line.split("#", 1)[0].strip()
        if not content:
            continue
        if len(content) > 4000 or any(ord(c) < 32 and c != "\t" for c in content):
            raise InputError(f"unsupported control character or long mailmap line {number}")
        if not ONE.fullmatch(content) and not TWO.fullmatch(content):
            raise InputError(f"invalid or unsupported mailmap syntax at line {number}")
    # Remove a BOM explicitly, but otherwise give Git the supplied map bytes.
    return text.encode("utf-8")


def contact(name, email):
    if (
        not email
        or len(name) + len(email) > 4000
        or any(ord(c) < 32 or c in "<>" for c in name + email)
        or any(c.isspace() for c in email)
    ):
        raise InputError("history contains a contact that cannot be safely resolved line-by-line")
    return f"{name} <{email}>" if name else f"<{email}>"


def history(repo, rev="HEAD", all_refs=False, max_commits=100000):
    if type(max_commits) is not int or not 1 <= max_commits <= 100000:
        raise InputError("max_commits must be 1..100000")
    repo = Path(repo).resolve()
    version = git(repo, "--version").decode("ascii", errors="replace")
    match = re.search(r"git version (\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (2, 39):
        raise InputError("Git 2.39 or newer is required")
    if all_refs:
        selection, scope = ["--all"], "all-refs"
    else:
        if not isinstance(rev, str) or not rev or rev.startswith("-"):
            raise InputError("revision must be one commit-ish, not an option or range")
        resolved = git(repo, "rev-parse", "--verify", "--end-of-options", rev + "^{commit}")
        scope = resolved.decode("ascii").strip()
        selection = [scope]
    raw = git(
        repo,
        "log",
        "--no-show-signature",
        "--no-use-mailmap",
        "--encoding=UTF-8",
        "-z",
        "--format=%H%x00%an%x00%ae%x00%cn%x00%ce",
        f"--max-count={max_commits + 1}",
        *selection,
        "--",
    )
    fields = raw.split(b"\x00")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 5:
        raise InputError("unexpected Git history record framing")
    if len(fields) // 5 > max_commits:
        raise InputError("history exceeds max_commits; refusing a partial impact report")
    if not fields:
        raise InputError("selected history has no commits")
    records = []
    try:
        for i in range(0, len(fields), 5):
            sha, an, ae, cn, ce = [f.decode("utf-8") for f in fields[i : i + 5]]
            if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha):
                raise InputError("invalid commit identifier from Git")
            records.append((sha, contact(an, ae), contact(cn, ce)))
    except UnicodeError as exc:
        raise InputError("history identities cannot be decoded as UTF-8") from exc
    shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true"
    return records, scope, shallow


def resolve(contacts, before, after):
    payload = ("\n".join(contacts) + "\n").encode("utf-8")
    results = []
    with tempfile.TemporaryDirectory(prefix="mailmap-impact-") as temp:
        scratch = Path(temp)
        git(scratch, "init", "-q", "--template=", "--initial-branch=main")
        for mapping in (before, after):
            (scratch / ".mailmap").write_bytes(mapping)
            output = git(scratch, "check-mailmap", "--stdin", data=payload)
            try:
                lines = output.decode("utf-8").splitlines()
            except UnicodeError as exc:
                raise InputError("Git returned non-UTF-8 contact data") from exc
            if len(lines) != len(contacts):
                raise InputError("Git returned an unexpected contact count")
            results.append(dict(zip(contacts, lines, strict=True)))
    return results


def compare(records, before, after, show_identities=False):
    authors = Counter(r[1] for r in records)
    committers = Counter(r[2] for r in records)
    contacts = sorted(authors.keys() | committers.keys())
    old, new = resolve(contacts, before, after)
    raw_ids = {value: f"identity-{i + 1}" for i, value in enumerate(contacts)}
    old_ids = {value: f"before-{i + 1}" for i, value in enumerate(sorted(set(old.values())))}
    new_ids = {value: f"after-{i + 1}" for i, value in enumerate(sorted(set(new.values())))}
    old_to_new, new_to_old = defaultdict(set), defaultdict(set)
    new_members, old_members = defaultdict(list), defaultdict(list)
    rows = []
    for value in contacts:
        a, b = old_ids[old[value]], new_ids[new[value]]
        old_to_new[a].add(b)
        new_to_old[b].add(a)
        new_members[b].append(raw_ids[value])
        old_members[a].append(raw_ids[value])
        row = dict(
            id=raw_ids[value],
            before=a,
            after=b,
            changed=old[value] != new[value],
            author_commits=authors[value],
            committer_commits=committers[value],
        )
        if show_identities:
            row.update(raw_contact=value, before_contact=old[value], after_contact=new[value])
        rows.append(row)
    merges = [
        dict(after=key, from_before=sorted(groups), members=new_members[key])
        for key, groups in sorted(new_to_old.items())
        if len(groups) > 1
    ]
    splits = [
        dict(before=key, to_after=sorted(groups), members=old_members[key])
        for key, groups in sorted(old_to_new.items())
        if len(groups) > 1
    ]
    changed = {v for v in contacts if old[v] != new[v]}
    return dict(
        schema_version=1,
        commits=len(records),
        raw_contacts=len(contacts),
        before_contacts=len(old_ids),
        after_contacts=len(new_ids),
        changed_contacts=len(changed),
        affected_author_commits=sum(authors[v] for v in changed),
        affected_committer_commits=sum(committers[v] for v in changed),
        affected_commits=sum(a in changed or c in changed for _, a, c in records),
        identities=rows,
        merges=merges,
        splits=splits,
        identities_included=show_identities,
        before_sha256=hashlib.sha256(before).hexdigest(),
        after_sha256=hashlib.sha256(after).hexdigest(),
    )


def audit(
    repo,
    before_path,
    after_path,
    rev="HEAD",
    all_refs=False,
    max_commits=100000,
    show_identities=False,
    fail_on=(),
):
    if not set(fail_on) <= {"changed", "merge", "split"}:
        raise InputError("unsupported failure policy")
    before, after = read_map(before_path), read_map(after_path)
    records, scope, shallow = history(repo, rev, all_refs, max_commits)
    report = compare(records, before, after, show_identities)
    report.update(scope=scope, shallow=shallow)
    report["warnings"] = ["shallow history: counts cover available commits only"] if shallow else []
    conditions = dict(
        changed=report["changed_contacts"] > 0,
        merge=bool(report["merges"]),
        split=bool(report["splits"]),
    )
    report["policy_failures"] = sorted(key for key in set(fail_on) if conditions[key])
    return report
