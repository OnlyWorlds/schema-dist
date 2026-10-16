"""Export a world folder to an Open Knowledge Format bundle.

    python walk/okf_export.py PATH/TO/WORLD-FOLDER OUT-DIR [--schema DIR]

Writes OUT-DIR (which must not exist yet) as a bundle in Google Cloud's Open
Knowledge Format (OKF) v0.2, as specified at one pinned commit:

    repo:   https://github.com/GoogleCloudPlatform/open-knowledge-format
    commit: ad30107c31c06aec8a7d5636e0d1058118604e6f  (SPEC.md "Version 0.2",
            2026-08-21, the ISO 8601 timestamp edit)

The bundle:

    index.md                       root index; frontmatter `okf_version: "0.2"`
    world.md                       the World as a concept (`type: World`),
                                   carrying the bundle metadata
    <type>/index.md                one per element type present
    <type>/<name-slug>--<id tail>.md   one concept per element

Each element concept:
  - `type` is the OnlyWorlds type, qualified as OKF's own examples qualify
    theirs (`BigQuery Table`): `OnlyWorlds Character`. `title` is the element's
    name, `description` its description, `generated` names this exporter and
    the element's `updated_at`. Never `verified`: an export confirms nothing.
  - scalar fields sit in the frontmatter under their OnlyWorlds names: `id`,
    `supertype`, `subtype`, `image_url` and every integer field. A field
    whose name OKF defines (§4.1, §5, §10: `status` and the like) would be
    written as `ow_<name>`; no frontmatter field needs it today.
  - every link field is in the frontmatter, keyed by its OnlyWorlds field
    name, as bundle-absolute paths (`location: /location/barovia--1a2b3c4d.md`,
    a list for a multi link; `element` for a pin's generic link), so the kind
    of each link survives as a key although OKF links are untyped (§6.1).
  - the body holds the type's own text fields under `# <Section>` /
    `## <Field>` headings in schema order, and every link again as a markdown
    link in a bullet (`- **location**: [Barovia](../location/....md)`), so
    viewers that draw the link graph from bodies draw it. Body links are
    relative: the reference viewer at the pinned commit skips `/` links.
  - unset values (the empty string, null, an empty list) are left out.
    Extension keys (`x_`, `atlas_`, `shadow_`) are carried as they are;
    server keys other than `updated_at` are not.

The schema is decoded through schema_walk.py beside this file, the one
decoder; this file reads no YAML of its own. Elements are read from
`elements/<type>/*.json` and the legacy `spatial/{map,pin,zone,marker}/`;
one id in both is served from `elements/` (folder format v0.3.8).

The output is deterministic: the same folder and the same dist give a
byte-identical bundle (UTF-8, LF, no export timestamp). Exit 0 written,
1 when the folder cannot be read as elements, 2 on bad arguments (a missing
folder, an existing OUT-DIR).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_walk import ELEMENT_TYPES, KNOWN_CATEGORIES, decode_schema  # noqa: E402

EXPORTER = "onlyworlds-okf-export/0.1"
OKF_VERSION = "0.2"
OKF_REPO = "https://github.com/GoogleCloudPlatform/open-knowledge-format"
OKF_COMMIT = "ad30107c31c06aec8a7d5636e0d1058118604e6f"

# Frontmatter keys OKF v0.2 gives a meaning (SPEC §4.1, §5, §10, §12). An
# OnlyWorlds field of the same name is written as `ow_<name>`.
OKF_KEYS = {"type", "title", "description", "resource", "tags", "sources",
            "usage_window", "generated", "verified", "status", "stale_after",
            "runtime", "parameters", "computation", "executor", "attester",
            "okf_version"}
EXTENSION_PREFIXES = ("x_", "atlas_", "shadow_")
LEGACY_SPATIAL = ("map", "pin", "zone", "marker")
# The OKF `type` of each slug (and the World), qualified: `OnlyWorlds Character`.
TYPE_NAMES = {slug: f"OnlyWorlds {title}" for title, slug in KNOWN_CATEGORIES.items()}


class FolderError(Exception):
    pass


# --- YAML: block style, newline-bearing strings double-quoted on one line, so
# no frontmatter line can ever read as the closing `---`.

class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, value):
    style = '"' if ("\n" in value or "\r" in value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_rep)


def _frontmatter(data: dict) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=1 << 30)


# --- reading the folder

def _read_json(path: Path, rel: str) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FolderError(f"{rel}: not JSON ({e})")
    if not isinstance(body, dict):
        raise FolderError(f"{rel}: not a JSON object")
    return body


def read_folder(src: Path, note) -> tuple[dict, list[tuple[str, dict]]]:
    """world.json and every element as (type slug, body), elements/ first."""
    world = _read_json(src / "world.json", "world.json")
    found: dict[str, tuple[str, dict, str]] = {}
    layouts = [("elements", ELEMENT_TYPES), ("spatial", LEGACY_SPATIAL)]
    for layout, slugs in layouts:
        for slug in slugs:
            folder = src / layout / slug
            for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                rel = path.relative_to(src).as_posix()
                body = _read_json(path, rel)
                eid = body.get("id")
                if not isinstance(eid, str) or not eid:
                    raise FolderError(f"{rel}: no `id`")
                if eid in found:
                    if found[eid][2].startswith("elements/") and layout == "spatial":
                        note(f"{rel}: id {eid} also at {found[eid][2]}; served elements/")
                        continue
                    raise FolderError(f"{rel}: duplicate id {eid} (also {found[eid][2]})")
                found[eid] = (slug, body, rel)
    return world, [(slug, body) for slug, body, _ in found.values()]


# --- names and links

def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:60].rstrip("-") or "element"


def assign_paths(elements: list[tuple[str, dict]]) -> dict[str, str]:
    """id -> bundle path (`character/the-consul--8b711e25.md`). Names are the
    slug plus the id's last 8 hex digits; a name two elements of one type would
    share falls back to the full id for both, so no order decides it."""
    stems: dict[str, tuple[str, str]] = {}
    for slug, body in elements:
        hexid = re.sub(r"[^0-9a-f]", "", body["id"].lower()) or "0"
        stems[body["id"]] = (slug, f"{slugify(str(body.get('name') or ''))}--{hexid[-8:]}")
    counts: dict[tuple[str, str], int] = {}
    for key in stems.values():
        counts[key] = counts.get(key, 0) + 1
    paths = {}
    for eid, (slug, stem) in stems.items():
        if counts[(slug, stem)] > 1:
            stem = stem.rsplit("--", 1)[0] + "--" + re.sub(r"[^0-9a-f]", "", eid.lower())
        paths[eid] = f"{slug}/{stem}.md"
    return paths


def _link_text(name: str) -> str:
    text = " ".join(str(name).split()) or "(unnamed)"
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _heading(name: str) -> str:
    words = name.replace("_", " ")
    return words[:1].upper() + words[1:]


def _timestamp(value) -> str | None:
    """An ISO 8601 datetime with an explicit offset (SPEC §5), else None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _unset(value) -> bool:
    return value is None or value == "" or value == []


def _fm_key(name: str) -> str:
    return f"ow_{name}" if name in OKF_KEYS else name


def _generated(body: dict) -> dict:
    gen = {"by": EXPORTER}
    at = _timestamp(body.get("updated_at"))
    if at:
        gen["at"] = at
    return gen


# --- one element

def element_doc(slug: str, body: dict, decoded: dict, paths: dict, names: dict,
                stats: dict) -> str:
    fm: dict = {"type": TYPE_NAMES[slug]}
    if not _unset(body.get("name")):
        fm["title"] = str(body["name"])
    if not _unset(body.get("description")):
        fm["description"] = str(body["description"])
    fm["generated"] = _generated(body)
    for f in decoded["base"]:
        if f["name"] in ("name", "description", "world"):
            continue
        if not _unset(body.get(f["name"])):
            fm[_fm_key(f["name"])] = body[f["name"]]

    here = paths[body["id"]]
    blocks: list[tuple[str, str]] = []   # (kind, text): kind "text" or "link"
    section = None

    def open_section(name: str) -> None:
        nonlocal section
        if section != name:
            section = name
            blocks.append(("text", f"# {name}"))

    def target(eid) -> tuple[str | None, str]:
        """(bundle-absolute path or None, bullet value) for one linked id."""
        stats["links"] += 1
        if isinstance(eid, str) and eid in paths:
            rel = "../" + paths[eid]
            return "/" + paths[eid], f"[{_link_text(names[eid])}]({rel})"
        stats["unresolved"].append(f"{here}: {eid}")
        return None, f"`{eid}` (not in this bundle)"

    for f in decoded["types"][slug]:
        kind, name = f["kind"], f["name"]
        if kind == "generic":
            eid = body.get(f["id_key"])
            if _unset(eid):
                continue
            path, shown = target(eid)
            fm[_fm_key(name)] = path if path else f"urn:uuid:{eid}"
            open_section(f["section"])
            blocks.append(("link", f"- **{name}**: {shown}"))
            continue
        value = body.get(name)
        if _unset(value):
            continue
        if kind == "scalar_str":
            open_section(f["section"])
            text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            blocks.append(("text", f"## {_heading(name)}\n\n{text}"))
        elif kind == "scalar_int":
            fm[_fm_key(name)] = value
        elif kind == "single":
            path, shown = target(value)
            fm[_fm_key(name)] = path if path else f"urn:uuid:{value}"
            open_section(f["section"])
            blocks.append(("link", f"- **{name}**: {shown}"))
        elif kind == "multi":
            pairs = [target(v) for v in value]
            fm[_fm_key(name)] = [p if p else f"urn:uuid:{v}" for (p, _), v in zip(pairs, value)]
            open_section(f["section"])
            blocks.append(("link", f"- **{name}**: " + ", ".join(s for _, s in pairs)))

    for key, value in body.items():
        if key.startswith(EXTENSION_PREFIXES):
            fm[key] = value

    out: list[str] = []
    for i, (kind, text) in enumerate(blocks):
        if i and not (kind == "link" and blocks[i - 1][0] == "link"):
            out.append("\n")
        out.append(text + "\n")
    return f"---\n{_frontmatter(fm)}---\n\n" + "".join(out)


# --- the world and the indexes

def read_version(schema_dir: Path) -> dict:
    """VERSION beside the schema directory: canonical, serial, published."""
    path = schema_dir.parent / "VERSION"
    found = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition(":")
            if sep:
                found[key.strip()] = value.strip()
    return found


def world_doc(world: dict, decoded: dict, version: dict, count: int) -> str:
    fm: dict = {"type": TYPE_NAMES["world"]}
    if not _unset(world.get("name")):
        fm["title"] = str(world["name"])
    if not _unset(world.get("description")):
        fm["description"] = str(world["description"])
    fm["generated"] = _generated(world)
    for f in decoded["world"]:
        if f["name"] in ("name", "description"):
            continue
        if not _unset(world.get(f["name"])):
            fm[_fm_key(f["name"])] = world[f["name"]]
    serial = version.get("serial", "unknown")
    fm["onlyworlds_export"] = {
        "exporter": EXPORTER,
        "schema": version.get("canonical", "unknown"),
        "schema_dist_serial": int(serial) if serial.isdigit() else serial,
        "okf_version": OKF_VERSION,
        "okf_spec": f"{OKF_REPO}/blob/{OKF_COMMIT}/SPEC.md",
        "elements": count,
    }
    return f"---\n{_frontmatter(fm)}---\n"


def index_entry(title: str, link: str, desc: str) -> str:
    suffix = f" - {_one_line(desc)}" if _one_line(desc) else ""
    return f"* [{_link_text(title)}]({link}){suffix}"


def build_bundle(src: Path, schema_dir: Path, note) -> tuple[dict[str, str], dict]:
    """Every bundle file as {bundle path: text}, and the export's counts."""
    decoded = decode_schema(schema_dir, note=note)
    world, elements = read_folder(src, note)
    paths = assign_paths(elements)
    names = {body["id"]: str(body.get("name") or "") for _, body in elements}
    stats = {"links": 0, "unresolved": [], "elements": len(elements)}
    files: dict[str, str] = {}
    by_type: dict[str, list[tuple[str, str, str]]] = {}
    for slug, body in elements:
        path = paths[body["id"]]
        files[path] = element_doc(slug, body, decoded, paths, names, stats)
        by_type.setdefault(slug, []).append(
            (names[body["id"]], path.split("/", 1)[1], str(body.get("description") or "")))

    version = read_version(schema_dir)
    files["world.md"] = world_doc(world, decoded, version, len(elements))
    root = [f'---\nokf_version: "{OKF_VERSION}"\n---\n', "# World\n",
            index_entry(str(world.get("name") or "World"), "world.md",
                        f"The world. OnlyWorlds schema {version.get('canonical', 'unknown')}; "
                        f"OKF {OKF_VERSION} at commit {OKF_COMMIT[:7]}."),
            "", "# Element types\n"]
    for slug in ELEMENT_TYPES:
        if slug not in by_type:
            continue
        entries = sorted(by_type[slug], key=lambda e: (e[0].casefold(), e[1]))
        n = len(entries)
        root.append(index_entry(TYPE_NAMES[slug], f"{slug}/index.md",
                                f"{n} element{'s' if n != 1 else ''}"))
        lines = [f"# {TYPE_NAMES[slug]}", ""]
        lines += [index_entry(t, link, d) for t, link, d in entries]
        files[f"{slug}/index.md"] = "\n".join(lines) + "\n"
    files["index.md"] = "\n".join(root) + "\n"
    stats["types"] = len(by_type)
    return files, stats


def write_bundle(files: dict[str, str], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    for rel in sorted(files):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[rel].encode("utf-8"))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path, help="a world folder (holds world.json and elements/)")
    ap.add_argument("out", type=Path, help="the bundle directory to create; must not exist")
    ap.add_argument("--schema", type=Path,
                    default=Path(__file__).resolve().parent.parent / "schema",
                    help="the schema YAML directory (default: the one beside this walk)")
    args = ap.parse_args(argv)
    if not (args.folder / "world.json").is_file():
        print(f"{args.folder}: not a world folder (no world.json)", file=sys.stderr)
        return 2
    if args.out.exists():
        print(f"{args.out}: already exists; give a new directory", file=sys.stderr)
        return 2
    if not args.schema.is_dir():
        print(f"{args.schema}: no schema directory; pass --schema DIR", file=sys.stderr)
        return 2

    def note(msg: str) -> None:
        print(f"note: {msg}", file=sys.stderr)

    try:
        files, stats = build_bundle(args.folder, args.schema, note)
    except FolderError as e:
        print(f"{e}", file=sys.stderr)
        return 1
    write_bundle(files, args.out)
    for line in stats["unresolved"]:
        print(f"unresolved link: {line}", file=sys.stderr)
    print(f"exported {stats['elements']} elements in {stats['types']} types to {args.out}: "
          f"{len(files)} files, {stats['links']} links, "
          f"{len(stats['unresolved'])} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
