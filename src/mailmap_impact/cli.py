import argparse
import json
import sys

from .core import InputError, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preview Git mailmap merges, splits and impact.")
    parser.add_argument("repo", help="existing local Git repository")
    parser.add_argument("--before", help="complete baseline mailmap; omitted means no rules")
    parser.add_argument("--after", required=True, help="complete proposed mailmap")
    refs = parser.add_mutually_exclusive_group()
    refs.add_argument(
        "--rev", default="HEAD", help="commit-ish and reachable history; default HEAD"
    )
    refs.add_argument("--all", action="store_true", dest="all_refs", help="all local refs")
    parser.add_argument("--max-commits", type=int, default=100000)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--show-identities", action="store_true", help="include names and emails")
    parser.add_argument(
        "--fail-on",
        action="append",
        choices=["changed", "merge", "split"],
        default=[],
        help="repeatable policy gate; default report-only",
    )
    parser.add_argument("--version", action="version", version="mailmap-impact 0.1.0")
    args = parser.parse_args(argv)
    try:
        report = audit(
            args.repo,
            args.before,
            args.after,
            args.rev,
            args.all_refs,
            args.max_commits,
            args.show_identities,
            args.fail_on,
        )
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print(
                f"{report['commits']} commits; {report['before_contacts']} -> "
                f"{report['after_contacts']} canonical contacts"
            )
            print(
                f"Changed: {report['changed_contacts']} contacts; affected: "
                f"{report['affected_commits']} commits (author "
                f"{report['affected_author_commits']}, committer "
                f"{report['affected_committer_commits']})"
            )
            print(f"{len(report['merges'])} merges, {len(report['splits'])} splits")
            for kind in ("merges", "splits"):
                for item in report[kind]:
                    print(f"{kind}: {json.dumps(item, ensure_ascii=True)}")
            for row in report["identities"]:
                if row["changed"]:
                    print(f"{row['id']}: {row['before']} -> {row['after']}")
                    if args.show_identities:
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in ("raw_contact", "before_contact", "after_contact")
                                },
                                ensure_ascii=True,
                            )
                        )
            for warning in report["warnings"]:
                print(f"warning: {warning}")
            if report["policy_failures"]:
                print("policy failed: " + ", ".join(report["policy_failures"]))
        return 1 if report["policy_failures"] else 0
    except (InputError, OSError) as exc:
        if args.format == "json":
            print(json.dumps(dict(schema_version=1, error=str(exc)), ensure_ascii=True))
        else:
            print(f"mailmap-impact: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
