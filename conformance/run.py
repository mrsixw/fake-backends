"""Run a conformance comparison and report every difference.

    uv run python -m conformance.run github --fake http://127.0.0.1:8080
    uv run python -m conformance.run jenkins --real http://127.0.0.1:8081 \\
        --fake http://127.0.0.1:8080

GitHub needs ``GITHUB_TOKEN`` (any token that can read public data; the
workflow's own is enough). Jenkins reads its credentials from
conformance/jenkins.py, which also provisions the real instance.

The report goes to stdout and, in Actions, to the job summary. The exit
status is non-zero when anything differs.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

from conformance.compare import Difference, Rules, Sample, compare
from conformance.probes import GITHUB_PROBES, JENKINS_PROBES, Probe

__all__ = ["Target", "main", "rules_for", "sample"]

RULES_FILE = Path(__file__).parent / "rules.yaml"
GITHUB_API = "https://api.github.com"
GITHUB_FIXTURE = "breakfast-fixtures"
GITHUB_FAKE_TOKEN = "token-mrsixw"
JENKINS_FIXTURE = "jeeves-fixtures"
JENKINS_USER = "jeeves-e2e"
JENKINS_FAKE_TOKEN = "jeeves-e2e-token"
REQUEST_TIMEOUT_SECONDS = 30
EXIT_DIFFERENT = 1


class Target:
    """Where to send probes, and how to authenticate there."""

    def __init__(
        self, base: str, headers: dict[str, str], graphql: str | None = None
    ) -> None:
        self.base = base.rstrip("/")
        self.graphql = graphql or f"{self.base}/graphql"
        self.session = requests.Session()
        self.session.headers.update(headers)


def sample(target: Target, probe: Probe) -> Sample:
    if probe.query is not None:
        response = target.session.post(
            target.graphql,
            json={"query": probe.query, "variables": probe.variables},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    else:
        response = target.session.get(
            f"{target.base}{probe.path}",
            params=probe.params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    if probe.text:
        body: Any = response.text
    else:
        try:
            body = response.json()
        except ValueError:
            body = None  # an HTML error page: compare its status alone
    return Sample(
        response.status_code, frozenset(k.lower() for k in response.headers), body
    )


def rules_for(service: str, probe: Probe) -> Rules:
    document = yaml.safe_load(RULES_FILE.read_text())[service]
    return Rules.merge(
        Rules.from_mapping(document.get("all")),
        Rules.from_mapping((document.get("probes") or {}).get(probe.name)),
    )


def _basic(user: str, token: str) -> dict[str, str]:
    return {
        "Authorization": "Basic "
        + base64.b64encode(f"{user}:{token}".encode()).decode()
    }


def _load_fixture(fake: str, name: str) -> None:
    response = requests.post(
        f"{fake.rstrip('/')}/_fake/fixtures/{name}", timeout=REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()


def _targets(args: argparse.Namespace) -> tuple[Target, Target, list[Probe]]:
    if args.service == "github":
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("set GITHUB_TOKEN to read real GitHub")
        _load_fixture(args.fake, GITHUB_FIXTURE)
        real = Target(args.real or GITHUB_API, {"Authorization": f"Bearer {token}"})
        fake = Target(args.fake, {"Authorization": f"Bearer {GITHUB_FAKE_TOKEN}"})
        return real, fake, GITHUB_PROBES
    from conformance import jenkins

    if not args.real:
        sys.exit("pass --real, the Jenkins conformance/jenkins.py provisioned")
    real_token = jenkins.provision(args.real)
    _load_fixture(args.fake, JENKINS_FIXTURE)
    fake = Target(args.fake, _basic(JENKINS_USER, JENKINS_FAKE_TOKEN))
    jenkins.run_first_build(fake)
    real = Target(args.real, _basic(JENKINS_USER, real_token))
    return real, fake, JENKINS_PROBES


def _report(service: str, results: list[tuple[Probe, list[Difference]]]) -> str:
    failed = [(probe, diffs) for probe, diffs in results if diffs]
    lines = [f"### Conformance: fake-{service}", ""]
    if not failed:
        lines.append(f"✅ All {len(results)} probes match.")
        return "\n".join(lines) + "\n"
    lines.append(f"❌ {len(failed)} of {len(results)} probes differ.")
    for probe, diffs in failed:
        lines += ["", f"**{probe.name}** (`{probe.path}`)", ""]
        lines += [f"- {d}" for d in diffs]
    lines += [
        "",
        "A difference that is intended belongs in conformance/rules.yaml, "
        "with a reason.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("service", choices=["github", "jenkins"])
    parser.add_argument("--fake", required=True, help="the fake's base URL")
    parser.add_argument("--real", help="the real service's base URL")
    args = parser.parse_args(argv)
    real, fake, probes = _targets(args)
    results = []
    for probe in probes:
        rules = rules_for(args.service, probe)
        results.append(
            (
                probe,
                compare(sample(real, probe), sample(fake, probe), rules, probe.headers),
            )
        )
    report = _report(args.service, results)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    return EXIT_DIFFERENT if any(diffs for _, diffs in results) else os.EX_OK


if __name__ == "__main__":
    sys.exit(main())
