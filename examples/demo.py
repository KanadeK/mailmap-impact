"""Create a temporary synthetic repository, exercise the installed CLI, and clean up."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mailmap_impact.core import git_env


def main():
    with tempfile.TemporaryDirectory(prefix="mailmap-demo-") as temporary:
        repo = Path(temporary)
        env = git_env()

        def git(*args, extra=None):
            subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                env=dict(env, **(extra or {})),
                capture_output=True,
            )

        git("init", "-q", "--template=", "--initial-branch=main")
        git("config", "user.name", "Release")
        git("config", "user.email", "release@example.test")
        for name, email in [("Old Alice", "alice@old.test"), ("Alice", "alice@new.test")]:
            git(
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "Demo",
                extra=dict(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email),
            )
        proposed = repo / "proposed.mailmap"
        proposed.write_text("Alice <alice@new.test> <alice@old.test>\n", encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "mailmap_impact",
                str(repo),
                "--after",
                str(proposed),
                *sys.argv[1:],
            ],
            env=os.environ,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
