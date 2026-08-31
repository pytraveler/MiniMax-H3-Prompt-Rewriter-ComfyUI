"""Keeping the translations in step with the nodes.

ComfyUI reads ``locales/<lang>/nodeDefs.json`` from every pack folder and shows
the node's own English whenever a key is missing there -- which is what makes a
partial translation safe, and what this tool exists to manage. It asks a running
ComfyUI what the nodes actually are, then says what a language file is missing
and what it carries that the nodes no longer have.

    python tools/locales.py report ru        what is missing, what has gone stale
    python tools/locales.py fill ru          add the missing keys, in English

``fill`` never touches a key that already has a translation and never deletes
one: it writes the English in as a placeholder so the file can be worked
through offline, and lists what it added. A key you have translated is yours.

Run it with ComfyUI up, since the node definitions are what it compares
against; there is no second copy of them to drift.
"""

from __future__ import annotations

import ast
import glob
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, "locales")
PACKAGE = os.path.join(ROOT, "minimax_h3_rewriter")
SERVER = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

NODE_KEYS = ("display_name", "description")
ITEM_KEYS = ("name", "tooltip")

TRANSLATE = ("display_name", "description", "tooltip")

RUNTIME = {"device"}


def own_nodes() -> set:
    """The classes this pack registers, read from its own source.

    Not a name prefix: other MiniMax-H3 packs exist and are installed beside
    this one, and translating their nodes from here would be both wrong and
    invisible. The mappings are read rather than imported because importing
    the package pulls in ComfyUI.
    """
    found = set()
    for path in glob.glob(os.path.join(PACKAGE, "*.py")):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "NODE_CLASS_MAPPINGS" not in names:
                continue
            if isinstance(node.value, ast.Dict):
                found |= {
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    if not found:
        raise SystemExit("no NODE_CLASS_MAPPINGS found in minimax_h3_rewriter/")
    return found


def definitions() -> dict:
    """This pack's nodes, as the frontend sees them."""
    with urllib.request.urlopen(f"{SERVER}/object_info", timeout=30) as answer:
        every = json.load(answer)
    ours = own_nodes()
    mine = {name: spec for name, spec in every.items() if name in ours}
    absent = sorted(ours - set(mine))
    if absent:
        print(f"warning: not loaded at {SERVER}, skipped: {', '.join(absent)}")
    if not mine:
        raise SystemExit(f"none of this pack's nodes are at {SERVER} -- is it loaded?")
    return mine


def wanted(spec: dict) -> dict:
    """Every translatable string of one node, as the English it starts from."""
    entry = {"display_name": spec.get("display_name") or "", "description": spec.get("description") or ""}
    inputs = {}
    for group in ("required", "optional"):
        for name, item in (spec.get("input", {}).get(group) or {}).items():
            meta = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
            inputs[name] = {"name": name, "tooltip": meta.get("tooltip") or ""}
    entry["inputs"] = inputs
    entry["outputs"] = {
        str(index): {"name": label}
        for index, label in enumerate(spec.get("output_name") or ())
    }
    return {key: value for key, value in entry.items() if value}


def load(lang: str) -> dict:
    path = os.path.join(LOCALES, lang, "nodeDefs.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def store(lang: str, data: dict) -> str:
    directory = os.path.join(LOCALES, lang)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "nodeDefs.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def walk(nodes: dict, every: bool = False):
    """Every translatable key, as ``(node, path tuple, english)``.

    ``every`` includes the keys this pack does not translate, which is what
    the staleness scan needs: a translation of one is worth reporting even
    though nothing will ask for it.
    """
    for node, spec in sorted(nodes.items()):
        english = wanted(spec)
        for key in NODE_KEYS:
            if english.get(key) and (every or key in TRANSLATE):
                yield node, (key,), english[key]
        for group in ("inputs", "outputs"):
            for item, fields in (english.get(group) or {}).items():
                if item in RUNTIME and not every:
                    continue
                for key in ITEM_KEYS:
                    if fields.get(key) and (every or key in TRANSLATE):
                        yield node, (group, item, key), fields[key]


def dig(data: dict, node: str, path: tuple):
    place = data.get(node)
    for step in path:
        if not isinstance(place, dict):
            return None
        place = place.get(step)
    return place


def put(data: dict, node: str, path: tuple, value: str) -> None:
    place = data.setdefault(node, {})
    for step in path[:-1]:
        place = place.setdefault(step, {})
    place[path[-1]] = value


def report(lang: str) -> int:
    nodes = definitions()
    have = load(lang)
    missing = {}
    for node, path, english in walk(nodes):
        if not dig(have, node, path):
            missing.setdefault(".".join(path[:1]) or "?", []).append(f"{node}.{'.'.join(path)}")

    live = {(node, path) for node, path, _ in walk(nodes, every=True)}
    stale = []
    for node, entry in sorted(have.items()):
        if node not in nodes:
            stale.append(node)
            continue
        for group in ("inputs", "outputs"):
            for item in (entry.get(group) or {}):
                if not any(p[:2] == (group, item) for n, p in live if n == node):
                    stale.append(f"{node}.{group}.{item}")

    total = sum(1 for _ in walk(nodes))
    done = total - sum(len(v) for v in missing.values())
    print(f"{lang}: {done}/{total} keys translated")
    for group, items in sorted(missing.items()):
        print(f"  missing {group}: {len(items)}")
        for name in items[:6]:
            print(f"    {name}")
        if len(items) > 6:
            print(f"    ... and {len(items) - 6} more")
    if stale:
        print(f"  STALE -- translated but no longer in the nodes: {len(stale)}")
        for name in stale:
            print(f"    {name}")
    return 1 if stale else 0


def fill(lang: str) -> int:
    nodes = definitions()
    have = load(lang)
    added = 0
    for node, path, english in walk(nodes):
        if not dig(have, node, path):
            put(have, node, path, english)
            added += 1
    path = store(lang, have)
    print(f"{path}: {added} key(s) added in English, ready to translate")
    return 0


def main(argv) -> int:
    if len(argv) != 3 or argv[1] not in ("report", "fill"):
        print(__doc__.strip().splitlines()[0])
        print("usage: python tools/locales.py {report|fill} <lang>")
        return 2
    return {"report": report, "fill": fill}[argv[1]](argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
