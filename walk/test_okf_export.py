"""Fixture tests for okf_export.py on the example world (example/hyperion).

    python walk/test_okf_export.py      (plain unittest)
    python -m pytest walk/test_okf_export.py

Needs only PyYAML, as the walk does. The OKF reference parser and viewer
(github.com/GoogleCloudPlatform/open-knowledge-format at the commit
okf_export.py pins) run too when importable: put a checkout's `src/` on
OKF_REFERENCE_SRC (they need only PyYAML) or install the reference agent;
otherwise those tests skip and say so.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import okf_export  # noqa: E402
from schema_walk import decode_schema  # noqa: E402

REPO = HERE.parent
HYPERION = REPO / "example" / "hyperion"
SCHEMA = REPO / "schema"
BODY_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")
EXTENSION_PREFIXES = ("x_", "atlas_", "shadow_")

if os.environ.get("OKF_REFERENCE_SRC"):
    sys.path.insert(0, os.environ["OKF_REFERENCE_SRC"])
try:
    from reference_agent.bundle.document import OKFDocument
    from reference_agent.viewer import generate_visualization
    REFERENCE = None
except ImportError as e:
    OKFDocument = generate_visualization = None
    REFERENCE = (f"OKF reference parser not importable ({e}); set OKF_REFERENCE_SRC "
                 f"to the src/ of an open-knowledge-format checkout at {okf_export.OKF_COMMIT[:7]}")


def run(src: Path, out: Path) -> tuple[int, str]:
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = okf_export.main([str(src), str(out), "--schema", str(SCHEMA)])
    return code, err.getvalue()


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def copy_world(dst: Path) -> Path:
    shutil.copytree(HYPERION, dst)
    return dst


def split(text: str) -> tuple[dict, str]:
    """Frontmatter and body, found the way the reference parser finds them:
    str.splitlines(), so every line break Python knows ends a line."""
    lines = text.splitlines()
    assert lines and lines[0] == "---", "no frontmatter"
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end])) or {}, "\n".join(lines[end + 1:])


def heading(name: str) -> str:
    words = name.replace("_", " ")
    return words[:1].upper() + words[1:]


def escape_text(text: str) -> str:
    """The body's text rule: a line markdown would read as a heading is escaped."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n"):
        if re.match(r" {0,3}#", line) or re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line):
            i = len(line) - len(line.lstrip(" "))
            line = line[:i] + "\\" + line[i:]
        out.append(line)
    return "\n".join(out)


def body_texts(body: str) -> dict[str, str]:
    found = re.finditer(r"^## ([^\n]+)\n\n(.*?)(?=\n\n#{1,2} |\n\n- \*\*|\Z)", body, re.S | re.M)
    return {m.group(1): m.group(2).rstrip("\n") for m in found}


def body_links(body: str) -> dict[str, list[str]]:
    found = re.finditer(r"^- \*\*(\w+)\*\*: (.*)$", body, re.M)
    return {m.group(1): BODY_LINK.findall(m.group(2)) for m in found}


def link_ids(src: dict, field: dict) -> list:
    if field["kind"] == "single":
        return [src[field["name"]]] if src.get(field["name"]) else []
    if field["kind"] == "multi":
        return list(src.get(field["name"]) or [])
    if field["kind"] == "generic":
        return [src[field["id_key"]]] if src.get(field["id_key"]) else []
    return []


class HyperionBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "bundle"
        cls.code, cls.err = run(HYPERION, cls.out)
        assert cls.code == 0, cls.err
        cls.files = tree(cls.out)
        cls.decoded = decode_schema(SCHEMA)
        cls.sources = {s["id"]: s for s in
                       (json.loads(p.read_text(encoding="utf-8"))
                        for p in sorted((HYPERION / "elements").rglob("*.json")))}
        cls.docs = {}
        for rel, data in cls.files.items():
            if rel.endswith(".md") and not rel.endswith("index.md") and rel != "world.md":
                cls.docs[rel] = split(data.decode("utf-8"))
        cls.by_id = {fm["id"]: rel for rel, (fm, _) in cls.docs.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def target_id(self, path: str, base: str | None = None) -> str:
        """The id inside the file a link names (bundle-absolute, or relative to base)."""
        rel = path[1:] if base is None else (Path(base).parent / path).as_posix()
        rel = os.path.normpath(rel).replace("\\", "/")
        self.assertIn(rel, self.docs, f"link {path} from {base}: no such concept")
        return self.docs[rel][0]["id"]

    def test_one_file_per_element(self):
        self.assertEqual(len(self.docs), len(self.sources))
        self.assertEqual(set(self.by_id), set(self.sources))

    def test_type_title_description(self):
        for rel, (fm, _) in self.docs.items():
            src = self.sources[fm["id"]]
            self.assertEqual(fm["type"], "OnlyWorlds " + src["type"].capitalize(), rel)
            self.assertEqual(fm["title"], src["name"], rel)
            self.assertEqual(fm.get("description", ""), src.get("description") or "", rel)
            self.assertEqual(fm["generated"], {"by": okf_export.EXPORTER, "at": src["updated_at"]})
            self.assertNotIn("verified", fm, rel)

    def test_scalars_and_extensions_match_source(self):
        """Base strings, every integer (0 included) and every extension key."""
        checked = 0
        for rel, (fm, _) in self.docs.items():
            src = self.sources[fm["id"]]
            for name in ("supertype", "subtype", "image_url"):
                self.assertEqual(fm.get(name, ""), src.get(name) or "", f"{rel}: {name}")
            for f in self.decoded["types"][src["type"]]:
                if f["kind"] == "scalar_int":
                    self.assertEqual(fm.get(f["name"]), src.get(f["name"]), f"{rel}: {f['name']}")
                    checked += src.get(f["name"]) == 0
            for key, value in src.items():
                if key.startswith(EXTENSION_PREFIXES):
                    self.assertEqual(fm.get(key), value, f"{rel}: {key}")
                    checked += 1
        self.assertGreater(checked, 1, "the fixture holds a 0 and an extension key")

    def test_text_fields_match_source(self):
        checked = 0
        for rel, (fm, body) in self.docs.items():
            src = self.sources[fm["id"]]
            texts = body_texts(body)
            expected = {heading(f["name"]): escape_text(src[f["name"]])
                        for f in self.decoded["types"][src["type"]]
                        if f["kind"] == "scalar_str" and src.get(f["name"])}
            self.assertEqual(texts, expected, rel)
            checked += len(expected)
        self.assertGreater(checked, 0)

    def test_every_link_names_the_right_element(self):
        """Frontmatter paths and body links, field by field, each target read by
        the id in its file: single, multi and generic, none missing, none extra."""
        checked = 0
        for rel, (fm, body) in self.docs.items():
            src = self.sources[fm["id"]]
            bullets = body_links(body)
            for f in self.decoded["types"][src["type"]]:
                if f["kind"] not in ("single", "multi", "generic"):
                    continue
                want = link_ids(src, f)
                if not want:
                    self.assertNotIn(f["name"], fm, rel)
                    self.assertNotIn(f["name"], bullets, rel)
                    continue
                value = fm.get(f["name"])
                self.assertIsNotNone(value, f"{rel}: {f['name']} missing")
                paths = value if f["kind"] == "multi" else [value]
                self.assertEqual([self.target_id(p) for p in paths], want, f"{rel}: {f['name']}")
                self.assertEqual([self.target_id(p, rel) for p in bullets.get(f["name"], [])],
                                 want, f"{rel}: body {f['name']}")
                checked += len(want)
        self.assertEqual(checked, 53)

    def test_indexes(self):
        root = self.files["index.md"].decode("utf-8")
        self.assertTrue(root.startswith('---\nokf_version: "0.2"\n---\n'))
        self.assertIn("* [OnlyWorlds Character](character/index.md) - 3 elements", root)
        self.assertTrue(self.files["character/index.md"].startswith(b"# OnlyWorlds Character\n"))
        listed = []
        for rel, data in self.files.items():
            if rel.endswith("/index.md"):
                self.assertFalse(data.startswith(b"---"), f"{rel}: frontmatter in an index")
                for link in BODY_LINK.findall(data.decode("utf-8")):
                    listed.append(self.target_id(link, rel))
        self.assertEqual(sorted(listed), sorted(self.sources))

    def test_world_metadata(self):
        fm, _ = split(self.files["world.md"].decode("utf-8"))
        meta = fm["onlyworlds_export"]
        self.assertEqual(fm["type"], "OnlyWorlds World")
        self.assertEqual(fm["title"], "Hyperion")
        self.assertEqual(fm["time_current"], 0)
        self.assertEqual(meta["schema"], okf_export.read_version(SCHEMA)["canonical"])
        self.assertIn(okf_export.OKF_COMMIT, meta["okf_spec"])
        self.assertEqual(meta["elements"], len(self.sources))

    def test_rerun_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = run(HYPERION, Path(tmp) / "bundle")
            self.assertEqual(code, 0)
            self.assertEqual(tree(Path(tmp) / "bundle"), self.files)

    def test_lf_utf8(self):
        for rel, data in self.files.items():
            self.assertNotIn(b"\r", data, rel)
            data.decode("utf-8")

    def test_refuses_existing_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run(HYPERION, Path(tmp))[0], 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    @unittest.skipIf(REFERENCE, REFERENCE)
    def test_reference_parser_and_viewer(self):
        """OKF's own code at the pinned commit: every concept parses and has a
        type, indexes carry no frontmatter but the root's okf_version, and the
        viewer draws one edge per distinct linked pair."""
        for rel, data in self.files.items():
            doc = OKFDocument.parse(data.decode("utf-8"))
            if rel.endswith("index.md"):
                allowed = {"okf_version"} if rel == "index.md" else set()
                self.assertLessEqual(set(doc.frontmatter), allowed, rel)
            else:
                doc.validate()
        pairs = {(s["id"], t) for s in self.sources.values()
                 for f in self.decoded["types"][s["type"]] for t in link_ids(s, f)
                 if t != s["id"]}
        with tempfile.TemporaryDirectory() as tmp:
            stats = generate_visualization(self.out, Path(tmp) / "viz.html")
        self.assertEqual(stats["concepts"], len(self.sources) + 1)
        self.assertEqual(stats["edges"], len(pairs))


class SpatialLayout(unittest.TestCase):
    def test_legacy_spatial_folder_exports_the_same_bundle(self):
        """Map, pin, zone and marker under the legacy spatial/, one zone also in
        elements/ under another name: the bundle is Hyperion's, byte for byte,
        with the elements/ copy served."""
        with tempfile.TemporaryDirectory() as tmp:
            src = copy_world(Path(tmp) / "world")
            (src / "spatial").mkdir()
            for slug in ("map", "pin", "zone", "marker"):
                shutil.move(str(src / "elements" / slug), str(src / "spatial" / slug))
            zone = next((src / "spatial" / "zone").glob("*.json"))
            shutil.copy(zone, src / "elements" / "zone.json")
            (src / "elements" / "zone").mkdir()
            shutil.move(str(src / "elements" / "zone.json"), str(src / "elements" / "zone" / zone.name))
            old = json.loads(zone.read_text(encoding="utf-8"))
            zone.write_text(json.dumps(dict(old, name="Swamps (stale copy)")), encoding="utf-8")
            code, err = run(src, Path(tmp) / "bundle")
            self.assertEqual(code, 0, err)
            self.assertIn("served elements/", err)
            with tempfile.TemporaryDirectory() as tmp2:
                run(HYPERION, Path(tmp2) / "bundle")
                self.assertEqual(tree(Path(tmp) / "bundle"), tree(Path(tmp2) / "bundle"))


class AwkwardFolder(unittest.TestCase):
    """Hyperion with hard cases: a frontmatter-breaking description, a name
    with brackets, two elements of one type sharing a name and id tail, and a
    link to an element the folder does not hold."""

    def test_awkward_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = copy_world(Path(tmp) / "world")
            chars = src / "elements" / "character"
            consul_path = next(chars.glob("the-consul--*.json"))
            consul = json.loads(consul_path.read_text(encoding="utf-8"))
            consul["name"] = "The [Consul]"
            consul["description"] = "line one\n---\nline: three"
            consul["friends"] = ["00000000-0000-4000-8000-000000000001"]
            consul_path.write_text(json.dumps(consul), encoding="utf-8")
            twin = dict(consul, id="11111111-1111-4111-8111-1111" + consul["id"][-8:])
            twin["friends"] = []
            (chars / "twin.json").write_text(json.dumps(twin), encoding="utf-8")

            out = Path(tmp) / "bundle"
            code, err = run(src, out)
            self.assertEqual(code, 0)
            self.assertIn("unresolved link", err)
            twins = sorted(p.name for p in (out / "character").glob("the-consul--*.md"))
            self.assertEqual(twins, sorted(f"the-consul--{i.replace('-', '')}.md"
                                           for i in (consul["id"], twin["id"])))
            docs = [split((out / "character" / n).read_text(encoding="utf-8"))[0] for n in twins]
            self.assertEqual(docs[0]["description"], "line one\n---\nline: three")
            self.assertEqual(docs[0]["title"], "The [Consul]")
            index = (out / "character" / "index.md").read_text(encoding="utf-8")
            self.assertIn("* [The \\[Consul\\]](", index)
            dangling = next(d for d in docs if "friends" in d)
            self.assertEqual(dangling["friends"], ["urn:uuid:00000000-0000-4000-8000-000000000001"])


class ReviewFindings(unittest.TestCase):
    """Kael's read of the branch (h29 #6371), one test per finding."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = copy_world(Path(self.tmp.name) / "world")
        self.out = Path(self.tmp.name) / "bundle"

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, rel_glob: str, change) -> dict:
        path = next(self.src.glob(rel_glob))
        body = json.loads(path.read_text(encoding="utf-8"))
        change(body)
        path.write_text(json.dumps(body), encoding="utf-8")
        return body

    def doc(self, rel_glob: str) -> tuple[dict, str]:
        return split(next(self.out.glob(rel_glob)).read_text(encoding="utf-8"))

    def test_2_2_every_line_break_is_quoted(self):
        """U+2028, U+2029, NEL and the other breaks str.splitlines() knows."""
        text = "one --- two ---\x85---\x0b---\x0c---\x1c---\x1d---\x1e---"
        self.edit("elements/character/the-consul--*.json",
                  lambda b: b.update(description=text, name="A ---"))
        code, err = run(self.src, self.out)
        self.assertEqual(code, 0, err)
        fm, _ = self.doc("character/a--*.md")
        self.assertEqual((fm["description"], fm["title"]), (text, "A ---"))
        if OKFDocument:
            doc = OKFDocument.parse(next(self.out.glob("character/a--*.md")).read_text(encoding="utf-8"))
            self.assertEqual(doc.frontmatter["description"], text)

    def test_2_3_unreadable_files_are_noted_and_skipped(self):
        chars = self.src / "elements" / "character"
        (chars / ".atlas-cache.json").write_text("[]", encoding="utf-8")
        (chars / "no-id.json").write_text('{"name": "Nobody"}', encoding="utf-8")
        (chars / "broken.json").write_text("{not json", encoding="utf-8")
        (chars / "list.json").write_text("[1, 2]", encoding="utf-8")
        code, err = run(self.src, self.out)
        self.assertEqual(code, 0, err)
        for name in (".atlas-cache.json", "no-id.json", "broken.json", "list.json"):
            self.assertIn(f"elements/character/{name}", err)
        self.assertEqual(len(list((self.out / "character").glob("*--*.md"))), 3)

    def test_2_4_world_keys_carried(self):
        world_path = self.src / "world.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        del world["time_current"]
        consul = next((self.src / "elements" / "character").glob("the-consul--*.json"))
        consul_id = json.loads(consul.read_text(encoding="utf-8"))["id"]
        world.update(time_range_current=7, x_calendar={"months": 12}, atlas_style="dark",
                     owner_character=consul_id, snapshot_label="ch05", snapshot_counts={"character": 3},
                     writable=False, api={"world_id": "w", "api_key": "secret"})
        world_path.write_text(json.dumps(world), encoding="utf-8")
        code, err = run(self.src, self.out)
        self.assertEqual(code, 0, err)
        fm, _ = split((self.out / "world.md").read_text(encoding="utf-8"))
        self.assertEqual(fm["time_current"], 7)
        self.assertNotIn("time_range_current", fm)
        for key in ("x_calendar", "atlas_style", "snapshot_label", "snapshot_counts",
                    "writable", "format_version"):
            self.assertEqual(fm[key], world[key], key)
        self.assertEqual(fm["owner_character"], "/character/" + consul.stem + ".md")
        self.assertNotIn("api", fm)
        self.assertNotIn("secret", (self.out / "world.md").read_text(encoding="utf-8"))

    def test_2_4_time_current_preferred(self):
        world_path = self.src / "world.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        world.update(time_current=3, time_range_current=9)
        world_path.write_text(json.dumps(world), encoding="utf-8")
        self.assertEqual(run(self.src, self.out)[0], 0)
        self.assertEqual(split((self.out / "world.md").read_text(encoding="utf-8"))[0]["time_current"], 3)

    def test_2_5_undeclared_keys_are_noted(self):
        self.edit("elements/character/the-consul--*.json",
                  lambda b: b.update(birthplace_old="somewhere", local_updated_at="2026-01-01T00:00:00Z"))
        code, err = run(self.src, self.out)
        self.assertEqual(code, 0, err)
        self.assertIn("`birthplace_old`", err)
        self.assertNotIn("local_updated_at", err)   # a folder key, known to the lint
        fm, _ = self.doc("character/the-consul--*.md")
        self.assertNotIn("birthplace_old", fm)

    def test_2_5_unknown_kind_is_noted(self):
        decoded = decode_schema(SCHEMA)
        decoded["types"]["character"] = decoded["types"]["character"] + [
            {"name": "aura", "kind": "scalar_float", "section": "Constitution"}]
        notes = []
        consul = next((HYPERION / "elements" / "character").glob("the-consul--*.json"))
        body = dict(json.loads(consul.read_text(encoding="utf-8")), aura=0.5)
        paths = okf_export.assign_paths([("character", body)])
        okf_export.element_doc("character", body, decoded, paths, {body["id"]: "x"},
                               {"links": 0, "unresolved": []}, notes.append)
        self.assertTrue(any("aura" in n and "scalar_float" in n for n in notes), notes)

    def test_nit_wrong_type_and_null_links_are_noted_and_left_out(self):
        chars = self.src / "elements" / "character"
        steinway = json.loads(next(chars.glob("mr-steinway--*.json")).read_text(encoding="utf-8"))
        self.edit("elements/character/the-consul--*.json",
                  lambda b: b.update(location=steinway["id"], objects=[None] + b["objects"]))
        self.edit("elements/pin/*.json", lambda b: b.update(element_type="location"))
        code, err = run(self.src, self.out)
        self.assertEqual(code, 0, err)
        fm, body = self.doc("character/the-consul--*.md")
        self.assertNotIn("location", fm)
        self.assertNotIn("**location**", body)
        self.assertEqual(len(fm["objects"]), 2)
        self.assertNotIn("None", body + json.dumps(fm))
        pin, pin_body = self.doc("pin/*--*.md")
        self.assertNotIn("element", pin)
        self.assertNotIn("**element**", pin_body)
        self.assertIn("`location`", err)
        self.assertIn("`objects`", err)
        self.assertIn("`element`", err)

    def test_nit_text_cannot_make_a_section(self):
        self.edit("elements/character/the-consul--*.json",
                  lambda b: b.update(background="# Fake section\n  ## Fake too\nplain\n===\n---"))
        self.assertEqual(run(self.src, self.out)[0], 0)
        fm, body = self.doc("character/the-consul--*.md")
        self.assertIn("\\# Fake section\n  \\## Fake too\nplain\n\\===\n\\---", body)
        self.assertNotIn("\n# Fake", body)

    def test_nit_hyperion_run_is_quiet(self):
        code, err = run(HYPERION, self.out)
        self.assertEqual((code, err), (0, ""))

    def test_nit_only_stderr_redirected(self):
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = okf_export.main([str(HYPERION), str(self.out), "--schema", str(SCHEMA)])
        self.assertEqual(code, 0)

    def test_nit_generated_by_is_explained(self):
        self.assertIn("human:", okf_export.__doc__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
