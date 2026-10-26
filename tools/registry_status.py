"""What the Comfy Registry's scan made of each published version.

Every version published to the registry is scanned, and one the scan flags
carries the reason in ``status_reason``: either the scanner's own report, a list
of YARA findings, or a policy verdict such as ``policy-v0.6: rce-remote-code``.
The registry's pages show neither, so this reads the API.

    python tools/registry_status.py                   the pack REPO points at
    python tools/registry_status.py --version 0.31.0  that version in detail
    python tools/registry_status.py --repo PATH       another pack's checkout

The node id and the local version come from the checkout's pyproject.toml, so
the same file serves any pack: set ``REPO`` to another checkout, or pass
``--repo``. Nothing is written, and nothing is sent but GET requests.

The API is eventually consistent -- one request can return the fresh report and
the next a stale ``Pending`` with no reason at all -- so the versions are asked
for a few times, the fullest answer is kept, and a disagreement is said.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    sys.exit("This tool needs Python 3.11 or newer, for tomllib.")

REPO = Path(__file__).resolve().parent.parent

API = "https://api.comfy.org"
REVIEWS = "https://github.com/Comfy-Org/registry-backend/issues"
TRIES = 3
PAUSE = 1.0
TIMEOUT = 30

PENDING = "NodeVersionStatusPending"


def short(status: str | None) -> str:
    """``NodeVersionStatusFlagged`` as ``Flagged``."""
    status = status or "?"
    for prefix in ("NodeVersionStatus", "NodeStatus"):
        if status.startswith(prefix):
            return status[len(prefix):]
    return status


def project(repo: Path) -> tuple[str, str]:
    """The node id and the version the checkout's pyproject.toml declares."""
    path = repo / "pyproject.toml"
    if not path.is_file():
        sys.exit(f"no pyproject.toml in {repo}")
    with path.open("rb") as handle:
        meta = tomllib.load(handle).get("project") or {}
    if not meta.get("name"):
        sys.exit(f"{path} has no [project] name, which is the registry's node id")
    return str(meta["name"]), str(meta.get("version") or "")


def fetch(path: str):
    """One GET against the registry, as JSON; None when it says there is no such thing."""
    request = urllib.request.Request(f"{API}{path}", headers={"User-Agent": "registry-status"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.load(answer)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def fullness(version: dict) -> tuple[int, int]:
    """How much one answer says about a version: scanned beats Pending, then the longer reason."""
    return int(version.get("status") != PENDING), len(version.get("status_reason") or "")


def versions(node: str, tries: int) -> tuple[list[dict], dict[str, collections.Counter]]:
    """The listed versions, newest first, each the fullest of ``tries`` answers.

    The second value names every version the answers disagreed about, with how
    often each status came back.
    """
    best: dict[str, dict] = {}
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    path = f"/nodes/{urllib.parse.quote(node)}/versions?include_status_reason=true"
    for attempt in range(tries):
        if attempt:
            time.sleep(PAUSE)
        for version in fetch(path) or []:
            key = version.get("id") or version.get("version")
            seen[key][short(version.get("status"))] += 1
            if key not in best or fullness(version) > fullness(best[key]):
                best[key] = version
    ordered = sorted(best.values(), key=lambda v: v.get("createdAt") or "", reverse=True)
    differed = {}
    for version in ordered:
        counts = seen[version.get("id") or version.get("version")]
        if len(counts) > 1 or sum(counts.values()) < tries:
            differed[version.get("version")] = counts
    return ordered, differed


def reading(reason: str | None) -> tuple[list[dict] | None, dict | None]:
    """``(findings, verdict)`` out of a ``status_reason``: the scanner's report, or the policy's word."""
    if not (reason or "").strip():
        return None, None
    try:
        parsed = json.loads(reason)
    except ValueError:
        return None, {"message": reason}
    if isinstance(parsed, list):
        return parsed, None
    if isinstance(parsed, dict):
        return None, parsed
    return None, {"message": reason}


def when(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def ago(moment: datetime | None) -> str:
    if moment is None:
        return "?"
    minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def counted(findings: list[dict]) -> str:
    """``12 findings, all info`` -- or the severities, when they differ."""
    levels = collections.Counter(str(f.get("severity") or "?") for f in findings)
    if len(levels) == 1:
        level = next(iter(levels))
        return f"{len(findings)} finding(s), all {level}"
    spread = ", ".join(f"{count} {level}" for level, count in levels.most_common())
    return f"{len(findings)} finding(s): {spread}"


def verdict_line(verdict: dict) -> str:
    message = str(verdict.get("message") or "").strip()
    found, _ = reading(message)
    if found is not None:
        message = f"scanner report, {counted(found)}"
    by = verdict.get("by")
    return f"{message} ({by})" if by else message


def summary(version: dict) -> str:
    """The one line the table gives a version."""
    if version.get("status") == PENDING:
        return f"scanning, {ago(when(version.get('createdAt')))} since it was published"
    findings, verdict = reading(version.get("status_reason"))
    if findings is not None:
        return f"scanner: {counted(findings)}, no policy verdict"
    if verdict is not None:
        return verdict_line(verdict)
    return "no reason given" if short(version.get("status")) != "Active" else "passed"


def key(finding: dict) -> tuple[str, str, str]:
    """What makes two findings the same one: the file, the rule and the line itself.

    Not the line number, which moves whenever anything above it changes.
    """
    return (
        str(finding.get("file_path") or ""),
        str(finding.get("issue_type") or ""),
        " ".join(str(finding.get("line_snippet") or "").split()),
    )


def show_findings(findings: list[dict]) -> None:
    for finding in sorted(findings, key=lambda f: (f.get("file_path") or "", f.get("line_number") or 0)):
        tags = ", ".join(finding.get("admin_tags") or ()) or "no tags"
        place = f"{finding.get('file_path')}:{finding.get('line_number')}"
        print(f"    {place}  {finding.get('issue_type')}  [{tags}]  {finding.get('severity')}")
        snippet = " ".join(str(finding.get("line_snippet") or "").split())
        if snippet:
            print(f"        {snippet}")


def compare(findings: list[dict], older: dict) -> None:
    """What this report has that the last scanned version's did not, and the other way."""
    earlier, _ = reading(older.get("status_reason"))
    now, before = collections.Counter(map(key, findings)), collections.Counter(map(key, earlier))
    if now == before:
        print(f"  The same {len(findings)} finding(s) as {older.get('version')}, line for line.")
        return
    kept = sum((now & before).values())
    print(f"  {kept} finding(s) the same as {older.get('version')}.")
    for title, delta in (("new since", now - before), ("gone since", before - now)):
        if not delta:
            continue
        print(f"  {sum(delta.values())} {title} {older.get('version')}:")
        for (file, rule, snippet), count in sorted(delta.items()):
            times = f" x{count}" if count > 1 else ""
            print(f"    {file}  {rule}{times}  {snippet}")


def detail(version: dict, listed: list[dict]) -> None:
    status = short(version.get("status"))
    print(f"{version.get('version')} in detail -- {status}, id {version.get('id')}")
    if version.get("status") == PENDING:
        print("  The scan has not reported yet; ask again later.")
        return
    findings, verdict = reading(version.get("status_reason"))
    if verdict is not None:
        print(f"  Verdict: {verdict_line(verdict)}")
        for step in verdict.get("statusHistory") or ():
            print(f"  Before that: {short(step.get('status'))}, {verdict_line(step)}")
    if findings is None:
        if verdict is None:
            print("  No reason is recorded." if status != "Active" else "  It passed.")
        return
    print(f"  The scanner's report: {counted(findings)}")
    show_findings(findings)
    newer = listed.index(version)
    older = next(
        (v for v in listed[newer + 1:] if reading(v.get("status_reason"))[0] is not None), None
    )
    if older is not None:
        compare(findings, older)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=REPO,
                        help="the pack's checkout, for its pyproject.toml (default: %(default)s)")
    parser.add_argument("--version", help="the version to show in detail (default: the checkout's own)")
    parser.add_argument("--tries", type=int, default=TRIES,
                        help="how many times to ask for the versions (default: %(default)s)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    node_id, local = project(args.repo)
    try:
        node = fetch(f"/nodes/{urllib.parse.quote(node_id)}")
        listed, differed = versions(node_id, max(1, args.tries)) if node else ([], {})
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"Could not read {API}: {error}", file=sys.stderr)
        return 2

    print(f"{node_id} -- the checkout at {args.repo} is {local or 'unversioned'}")
    if node is None:
        print("The registry has no node by that id: nothing has been published under it yet.")
        return 0
    latest = (node.get("latest_version") or {}).get("version") or "none"
    publisher = (node.get("publisher") or {}).get("id") or "?"
    print(
        f"Node {short(node.get('status'))}, publisher {publisher}, {node.get('downloads', 0)} "
        f"downloads; the registry's latest version is {latest}"
    )
    print(f"Review requests: {REVIEWS}?q={urllib.parse.quote_plus(node_id)}")
    print()

    if not listed:
        print("No versions are listed.")
        return 0
    width = max(len(str(v.get("version"))) for v in listed)
    for version in listed:
        moment = when(version.get("createdAt"))
        stamp = moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "?"
        print(f"  {str(version.get('version')):<{width}}  {short(version.get('status')):<8}  "
              f"{stamp}  {summary(version)}")
    for name, counts in differed.items():
        said = ", ".join(f"{status} x{count}" for status, count in counts.items())
        print(f"  The answers about {name} differed ({said}); the fullest one is shown.")
    print()

    wanted = args.version or local
    chosen = next((v for v in listed if v.get("version") == wanted), None)
    if chosen is None:
        if args.version:
            print(f"{args.version} is not among the listed versions.")
            return 1
        chosen = listed[0]
    detail(chosen, listed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
