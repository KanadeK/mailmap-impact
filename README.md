# mailmap-impact

Preview a `.mailmap` change before merging it. Compare two complete mapping files
against the raw authors and committers in a local Git history, using **Git's own
resolver**. See which displayed contacts merge, split or change, with exact counts
of affected author roles, committer roles and distinct commits.

No history rewrite, network access, credential requirement or source checkout
changes. Names and emails are omitted unless you pass `--show-identities`.

## Install

Requires Python 3.11+ and Git 2.39+ on PATH. No runtime Python dependencies.

```sh
git clone https://github.com/KanadeK/mailmap-impact.git
cd mailmap-impact
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
```

Uninstall with `python -m pip uninstall mailmap-impact`, then remove the virtual
environment if desired. Scratch repositories are created in the system temporary
directory and automatically removed. No cache or report file is written.

## Try an isolated synthetic demo

```sh
python examples/demo.py
```

The demo creates a disposable Git repository with two synthetic author contacts and
a separate committer, runs the real CLI, and removes the fixture. Expected: 2 commits,
3 -> 2 canonical contacts, 1 affected author commit, 0 affected committer commits,
1 merge and 0 splits. Add `--show-identities` to the demo for readable contacts.

## Check a real proposed change

Save complete baseline and proposed maps as UTF-8 files. Pass their paths explicitly:

```sh
mailmap-impact /path/to/repo --before old.mailmap --after proposed.mailmap
mailmap-impact /path/to/repo --before old.mailmap --after proposed.mailmap --show-identities
mailmap-impact /path/to/repo --before old.mailmap --after proposed.mailmap --format json --fail-on merge --fail-on split
```

Omitting `--before` compares against **no mapping rules**. The tool deliberately
ignores the source repository's `.mailmap`, `mailmap.file` and `mailmap.blob` when
resolving contacts: supply every intended rule in each file. It resolves each map
in an isolated temporary repository, with global/system/environment Git config
excluded. It never installs the proposed map in the source repository.

Default scope is commits reachable from HEAD. `--rev v1.2.0` selects one commit-ish
and its ancestors; ranges are not supported. `--all` instead uses all local refs,
including local remote-tracking branches, tags and stash refs. Shallow history is
explicitly labeled and covers available commits only. Fetching is never performed.

## Interpreting the report

- A **contact** is the exact `Name <email>` string returned by Git. It is not a
  verified person or account. Contacts with different names remain different even
  when their emails match, unless the supplied rules make the outputs equal.
- A **merge** means multiple baseline contact groups become one proposed group.
  A **split** means one baseline group becomes multiple proposed groups.
- A one-to-one rename counts as changed, but is neither a merge nor a split.
- Author and committer counts may overlap; `affected_commits` counts each observed
  commit once if either role changes. Merge/split groups consider both roles together.
- `identity-*`, `before-*` and `after-*` labels are deterministic for this report's
  sorted contact sets. They are not persistent identifiers across histories.
- Reports omit real identities by default. Explicit `--show-identities` adds raw,
  baseline and proposed contacts. JSON escapes non-ASCII/control characters.

The JSON contract (`schema_version: 1`) includes summary counts, per-identity role
counts and transitions, `merges`/`splits` with member witnesses, map SHA-256 values,
selected scope, shallow-history warnings and `policy_failures`. Map hashes refer
to the UTF-8 bytes supplied to Git after stripping an optional BOM.

Exit codes: **0** report generated and no selected gate triggered; **1** selected
gate triggered; **2** invalid input or environment failure. Default is report-only.
Repeat `--fail-on changed`, `--fail-on merge`, or `--fail-on split` to gate only the
changes relevant to your team. Input errors contain an `error` string in JSON mode;
argparse usage errors go to stderr. Native Git error diagnostics can contain paths.

## Boundaries and alternatives

All four [documented mailmap forms](https://git-scm.com/docs/gitmailmap) are supported.
Git decides case matching and repeated-rule precedence. A conservative syntax check
rejects malformed lines instead of allowing Git to silently ignore them. UTF-8 with
optional BOM and LF/CRLF is supported; maps are at most 4 MiB and effective lines at
most 4,000 characters. A map can still contain unused rules; this is an impact
comparison, not a completeness linter.

At most 100,000 commits (`--max-commits` may lower the cap). Exceeding the cap is an
error, never a truncated success. Each Git command has a 60-second timeout. History
and Git output are buffered in memory; output above 64 MiB is rejected after capture.
This is not a hard memory sandbox. Unusual malformed/non-UTF-8 contacts are rejected.
The source repository should be a trusted, locally available Git repository.

This does not inspect co-author trailers, infer that similarly named people are the
same person, rewrite commits, or predict/change GitHub's hosted contributor graph.
Use native `git check-mailmap` for single lookups and
[mailmap-checker](https://github.com/cansarigol/mailmap-checker) for completeness
checking. This project adds before/after group transitions and role-aware counts.
No third-party source is bundled. MIT. [Contributing](CONTRIBUTING.md),
[security reporting](SECURITY.md).

## 中文说明

修改 mailmap 前，使用原生 Git 在隔离目录中计算修改前后的联系人映射，展示合并、拆分、
名称变化及受影响提交数量。默认不输出姓名和邮箱；需要时显式加 `--show-identities`。
不会改写 Git 历史，也不证明联系人属于同一真人，更不保证 GitHub 贡献者图如何变化。
必须提供完整映射文件，未传 `--before` 表示原始身份；私有报告分享前请自行检查。
