import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mailmap_impact.core import InputError, audit, compare, git_env, history, read_map, resolve

A = "Old Alice <alice@old.test>"
B = "Alice <alice@new.test>"
C = "Release <release@example.test>"
MERGE = b"Alice <alice@new.test> <alice@old.test>\n"


class MappingTests(unittest.TestCase):
    def test_merge_and_role_counts(self):
        records = [("a", A, C), ("b", B, A), ("c", A, A)]
        report = compare(records, b"", MERGE)
        self.assertEqual(report["before_contacts"], 3)
        self.assertEqual(report["after_contacts"], 2)
        self.assertEqual(report["affected_author_commits"], 2)
        self.assertEqual(report["affected_committer_commits"], 2)
        self.assertEqual(report["affected_commits"], 3)
        self.assertEqual(len(report["merges"]), 1)
        self.assertEqual(report["splits"], [])
        self.assertEqual(len(report["merges"][0]["members"]), 2)

    def test_split_on_removal(self):
        report = compare([("a", A, B)], MERGE, b"")
        self.assertEqual(len(report["splits"]), 1)
        self.assertEqual(report["merges"], [])

    def test_rename_without_merge(self):
        report = compare([("a", A, A)], b"", MERGE)
        self.assertEqual(report["changed_contacts"], 1)
        self.assertEqual(report["merges"], [])
        self.assertEqual(report["affected_commits"], 1)

    def test_unchanged(self):
        report = compare([("a", A, B)], MERGE, MERGE)
        self.assertEqual(report["changed_contacts"], 0)
        self.assertEqual(report["affected_commits"], 0)

    def test_unmatched_rule_has_no_impact(self):
        report = compare([("a", B, B)], b"", MERGE)
        self.assertEqual(report["changed_contacts"], 0)

    def test_default_output_contains_no_names_or_emails(self):
        encoded = json.dumps(compare([("a", A, B)], b"", MERGE))
        self.assertNotIn("Alice", encoded)
        self.assertNotIn("@", encoded)

    def test_opt_in_identity_display(self):
        report = compare([("a", A, B)], b"", MERGE, show_identities=True)
        self.assertIn(A, [row["raw_contact"] for row in report["identities"]])
        self.assertTrue(report["identities_included"])

    def test_native_git_four_forms(self):
        examples = [
            (
                b"Canonical <old@example.test>\n",
                "Wrong <old@example.test>",
                "Canonical <old@example.test>",
            ),
            (
                b"<new@example.test> <old@example.test>\n",
                "Wrong <old@example.test>",
                "Wrong <new@example.test>",
            ),
            (
                b"Canonical <new@example.test> <old@example.test>\n",
                "Wrong <old@example.test>",
                "Canonical <new@example.test>",
            ),
            (
                b"Canonical <new@example.test> Wrong <old@example.test>\n",
                "Wrong <old@example.test>",
                "Canonical <new@example.test>",
            ),
        ]
        for mapping, old, expected in examples:
            with self.subTest(mapping=mapping):
                self.assertEqual(resolve([old], b"", mapping)[1][old], expected)

    def test_name_specific_rule_does_not_match_other_name(self):
        value = "Other <old@example.test>"
        mapping = b"Canonical <new@example.test> Wrong <old@example.test>\n"
        self.assertEqual(resolve([value], b"", mapping)[1][value], value)

    def test_native_case_insensitive_matching(self):
        value = "OLD ALICE <ALICE@OLD.TEST>"
        self.assertEqual(resolve([value], b"", MERGE)[1][value], B)

    def test_native_duplicate_rule_precedence(self):
        mapping = b"First <a@test> <old@test>\nLast <b@test> <old@test>\n"
        value = "Old <old@test>"
        self.assertEqual(resolve([value], b"", mapping)[1][value], "Last <b@test>")

    def test_unicode(self):
        value = "旧名 <old@test>"
        mapping = "新名 <new@test> <old@test>\n".encode()
        self.assertEqual(resolve([value], b"", mapping)[1][value], "新名 <new@test>")

    def test_order_independent_group_labels(self):
        rows = [("a", A, B), ("b", B, C)]
        self.assertEqual(compare(rows, b"", MERGE), compare(rows[::-1], b"", MERGE))

    def test_environment_overrides_do_not_contaminate_resolver(self):
        with patch.dict(
            os.environ,
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "mailmap.file",
                "GIT_CONFIG_VALUE_0": "nonexistent",
                "GIT_DIR": "bad",
            },
        ):
            self.assertNotIn("GIT_DIR", git_env())
            self.assertNotIn("GIT_CONFIG_COUNT", git_env())
            self.assertEqual(resolve([A], b"", MERGE)[1][A], B)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_git("init", "-q", "--template=", "--initial-branch=main")
        self.run_git("config", "user.name", "Release")
        self.run_git("config", "user.email", "release@example.test")
        self.after = self.root / "after.mailmap"
        self.after.write_bytes(MERGE)
        self.before = self.root / "before.mailmap"
        self.before.write_bytes(b"")

    def run_git(self, *args, env=None):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            env=env or git_env(),
            capture_output=True,
            check=True,
        )
        return result.stdout.decode().strip()

    def commit(self, name, email):
        env = git_env()
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email)
        self.run_git(
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "Synthetic fixture",
            env=env,
        )

    def populated(self):
        self.commit("Old Alice", "alice@old.test")
        self.commit("Alice", "alice@new.test")

    def test_real_history_and_no_repository_changes(self):
        self.populated()
        (self.repo / ".mailmap").write_text("Intruder <wrong@test> <alice@old.test>\n")
        self.run_git("config", "mailmap.file", str(self.repo / ".mailmap"))
        self.run_git("config", "log.mailmap", "true")
        original_map = (self.repo / ".mailmap").read_bytes()
        original_config = (self.repo / ".git/config").read_bytes()
        head = self.run_git("rev-parse", "HEAD")
        status = self.run_git("status", "--porcelain")
        report = audit(self.repo, self.before, self.after, show_identities=True, fail_on=["merge"])
        self.assertEqual(report["commits"], 2)
        self.assertEqual(report["affected_author_commits"], 1)
        self.assertEqual(report["affected_committer_commits"], 0)
        self.assertEqual(report["policy_failures"], ["merge"])
        self.assertNotIn("Intruder", json.dumps(report))
        self.assertEqual((self.repo / ".mailmap").read_bytes(), original_map)
        self.assertEqual((self.repo / ".git/config").read_bytes(), original_config)
        self.assertEqual(self.run_git("rev-parse", "HEAD"), head)
        self.assertEqual(self.run_git("status", "--porcelain"), status)

    def test_selected_revision(self):
        self.populated()
        self.assertEqual(audit(self.repo, None, self.after, rev="HEAD~1")["commits"], 1)

    def test_all_refs_includes_other_branch(self):
        self.populated()
        self.run_git("checkout", "-q", "-b", "other", "HEAD~1")
        self.commit("Third", "third@example.test")
        self.run_git("checkout", "-q", "main")
        report = audit(self.repo, None, self.after, all_refs=True)
        self.assertEqual(report["commits"], 3)
        self.assertEqual(report["scope"], "all-refs")

    def test_history_cap_is_an_error_not_truncation(self):
        self.populated()
        with self.assertRaises(InputError):
            audit(self.repo, None, self.after, max_commits=1)

    def test_empty_repo(self):
        with self.assertRaises(InputError):
            audit(self.repo, None, self.after, all_refs=True)

    def test_invalid_revision_and_option(self):
        self.populated()
        for value in ("does-not-exist", "--all", "HEAD~1..HEAD"):
            with self.subTest(value=value), self.assertRaises(InputError):
                audit(self.repo, None, self.after, rev=value)

    def test_malformed_map_not_silently_ignored(self):
        for data in (b"not a mapping", b"A <bad> trailing junk", b"A <x\x00y>", b"\xff"):
            self.after.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(InputError):
                read_map(self.after)

    def test_bom_crlf_and_comments(self):
        self.after.write_bytes(b"\xef\xbb\xbf# comment\r\n" + MERGE.replace(b"\n", b"\r\n"))
        result = read_map(self.after)
        self.assertFalse(result.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(resolve([A], b"", result)[1][A], B)

    def test_oversized_map(self):
        with patch("mailmap_impact.core.MAX_MAP_BYTES", 5), self.assertRaises(InputError):
            read_map(self.after)

    def test_nonstandard_line_separators_rejected(self):
        for separator in ("\v", "\f", "\r", "\x85", "\u2028", "\u2029"):
            self.after.write_bytes(("A <a@test>" + separator + "B <b@test>").encode())
            with self.subTest(separator=separator), self.assertRaises(InputError):
                read_map(self.after)

    def test_old_git_version_is_rejected(self):
        with patch("mailmap_impact.core.git", return_value=b"git version 2.30.0\n"):
            with self.assertRaises(InputError):
                history(self.repo)

    def test_symlink_map_rejected(self):
        target = self.root / "link.mailmap"
        try:
            os.symlink(self.after, target)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with self.assertRaises(InputError):
            read_map(target)

    def test_shallow_history_labeled(self):
        self.populated()
        head = self.run_git("rev-parse", "HEAD")
        (self.repo / ".git/shallow").write_text(head + "\n")
        report = audit(self.repo, None, self.after)
        self.assertTrue(report["shallow"])
        self.assertEqual(report["commits"], 1)
        self.assertTrue(report["warnings"])

    def test_missing_git_and_timeout_are_input_errors(self):
        self.populated()
        for exc in (OSError("not found"), subprocess.TimeoutExpired("git", 60)):
            with patch("mailmap_impact.core.subprocess.run", side_effect=exc):
                with self.assertRaises(InputError):
                    history(self.repo)

    def test_cli_workflow(self):
        self.populated()

        def run(*args):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mailmap_impact",
                    str(self.repo),
                    "--after",
                    str(self.after),
                    "--format",
                    "json",
                    *args,
                ],
                capture_output=True,
                text=True,
            )

        report = run()
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(len(json.loads(report.stdout)["merges"]), 1)
        self.assertNotIn("alice@", report.stdout)
        self.assertEqual(run("--fail-on", "merge").returncode, 1)
        self.assertEqual(run("--fail-on", "split").returncode, 0)
        self.assertIn("alice@", run("--show-identities").stdout)
        self.assertEqual(run("--rev", "missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
