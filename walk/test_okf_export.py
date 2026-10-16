"""Fixture tests for okf_export.py on the example world (example/hyperion).

    python walk/test_okf_export.py      (plain unittest)
    python -m pytest walk/test_okf_export.py

Needs only PyYAML, as the walk does.
"""

from __future__ import annotations

import io
import json
import re
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


def export(out: Path) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return okf_export.main([str(HYPERION), str(out), "--schema", str(SCHEMA)])


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def split(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n"), "no frontmatter"
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head) or {}, body


class HyperionBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "bundle"
        assert export(cls.out) == 0
        cls.files = tree(cls.out)
        cls.decoded = decode_schema(SCHEMA)
        cls.sources = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted((HYPERION / "elements").rglob("*.json"))]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def concepts(self) -> dict[str, str]:
        return {rel: data.decode("utf-8") for rel, data in self.files.items()
                if rel.endswith(".md") and rel.rsplit("/", 1)[-1] != "index.md"
                and rel != "world.md"}

    def test_one_file_per_element(self):
        concepts = self.concepts()
        self.assertEqual(len(concepts), len(self.sources))
        ids = {split(text)[0]["id"] for text in concepts.values()}
        self.assertEqual(ids, {s["id"] for s in self.sources})

    def test_type_and_title(self):
        by_id = {s["id"]: s for s in self.sources}
        for rel, text in self.concepts().items():
            fm, _ = split(text)
            src = by_id[fm["id"]]
            self.assertEqual(fm["type"], "OnlyWorlds " + src["type"].capitalize(), rel)
            self.assertEqual(fm["title"], src["name"], rel)
            self.assertNotIn("verified", fm, rel)

    def test_every_link_resolves(self):
        checked = 0
        for rel, data in self.files.items():
            if not rel.endswith(".md"):
                continue
            text = data.decode("utf-8")
            for target in BODY_LINK.findall(text):
                path = (self.out / rel).parent / target
                self.assertTrue(path.is_file(), f"{rel}: body link {target}")
                checked += 1
            if rel in self.concepts():
                fm, _ = split(text)
                slug = rel.split("/", 1)[0]
                for f in self.decoded["types"][slug]:
                    if f["kind"] not in ("single", "multi", "generic") or f["name"] not in fm:
                        continue
                    values = fm[f["name"]]
                    for v in values if isinstance(values, list) else [values]:
                        self.assertTrue(v.startswith("/"), f"{rel}: {f['name']} {v}")
                        self.assertTrue((self.out / v[1:]).is_file(), f"{rel}: {f['name']} {v}")
                        checked += 1
        self.assertGreater(checked, 0)

    def test_every_source_link_is_carried(self):
        expected = 0
        for s in self.sources:
            for f in self.decoded["types"][s["type"]]:
                if f["kind"] == "single" and s.get(f["name"]):
                    expected += 1
                elif f["kind"] == "multi":
                    expected += len(s.get(f["name"]) or [])
                elif f["kind"] == "generic" and s.get(f["id_key"]):
                    expected += 1
        body_links = sum(len(BODY_LINK.findall(t.split("\n---\n", 1)[1]))
                         for t in self.concepts().values())
        self.assertEqual(body_links, expected)

    def test_indexes(self):
        root = self.files["index.md"].decode("utf-8")
        self.assertTrue(root.startswith('---\nokf_version: "0.2"\n---\n'))
        for rel, data in self.files.items():
            if rel.endswith("/index.md"):
                self.assertFalse(data.startswith(b"---"), f"{rel}: frontmatter in an index")
        self.assertIn("* [OnlyWorlds Character](character/index.md) - 3 elements", root)
        self.assertTrue(self.files["character/index.md"].startswith(b"# OnlyWorlds Character\n"))
        listed = sum(data.decode("utf-8").count("\n* [")
                     for rel, data in self.files.items() if rel.endswith("/index.md"))
        self.assertEqual(listed, len(self.sources))

    def test_world_metadata(self):
        fm, _ = split(self.files["world.md"].decode("utf-8"))
        meta = fm["onlyworlds_export"]
        version = okf_export.read_version(SCHEMA)
        self.assertEqual(fm["type"], "OnlyWorlds World")
        self.assertEqual(fm["title"], "Hyperion")
        self.assertEqual(meta["schema"], version["canonical"])
        self.assertIn(okf_export.OKF_COMMIT, meta["okf_spec"])
        self.assertEqual(meta["elements"], len(self.sources))

    def test_rerun_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            again = Path(tmp) / "bundle"
            self.assertEqual(export(again), 0)
            self.assertEqual(tree(again), self.files)

    def test_lf_utf8(self):
        for rel, data in self.files.items():
            self.assertNotIn(b"\r", data, rel)
            data.decode("utf-8")

    def test_refuses_existing_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(export(Path(tmp)), 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class AwkwardFolder(unittest.TestCase):
    """Hyperion with hard cases: a frontmatter-breaking description, a name
    with brackets, two elements of one type sharing a name and id tail, and a
    link to an element the folder does not hold."""

    def test_awkward_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "world"
            for rel, data in tree(HYPERION).items():
                (src / rel).parent.mkdir(parents=True, exist_ok=True)
                (src / rel).write_bytes(data)
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
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                code = okf_export.main([str(src), str(out), "--schema", str(SCHEMA)])
            self.assertEqual(code, 0)
            self.assertIn("unresolved link", err.getvalue())
            twins = sorted(p.name for p in (out / "character").glob("the-consul--*.md"))
            self.assertEqual(twins, sorted(f"the-consul--{i.replace('-', '')}.md"
                                           for i in (consul["id"], twin["id"])))
            fm, body = split((out / "character" / twins[0]).read_text(encoding="utf-8"))
            self.assertEqual(fm["description"], "line one\n---\nline: three")
            self.assertEqual(fm["title"], "The [Consul]")
            index = (out / "character" / "index.md").read_text(encoding="utf-8")
            self.assertIn("* [The \\[Consul\\]](", index)
            dangling = next(split((out / "character" / n).read_text(encoding="utf-8"))[0]
                            for n in twins
                            if "friends" in split((out / "character" / n)
                                                  .read_text(encoding="utf-8"))[0])
            self.assertEqual(dangling["friends"], ["urn:uuid:00000000-0000-4000-8000-000000000001"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
