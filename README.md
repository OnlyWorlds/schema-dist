# OnlyWorlds schema-dist

Generated artifacts only. Nobody edits this repo by hand; keel (the OnlyWorlds
platform) regenerates and publishes it after any schema change. If you found a
problem here, the fix happens upstream — open an issue, don't send a PR
against generated files.

## What this is

- `schema/*.yaml` — the 22 OnlyWorlds element types + `base_properties` +
  `world`, byte-identical to the Council-governed standard
  (github.com/OnlyWorlds/OnlyWorlds). No presentation keys.
- `presentation.json` — family + icon **defaults** per type. Defaults, not
  authority: tools are free to override (Atlas remaps wholesale for dark
  mode). The colour values carry their CVD-validation provenance — they were
  measured, not tasted; check the provenance before "fixing" one.
- `walk/schema_walk.py` — THE official schema reader. Vendor it or port it,
  but keep its semantics; it is the one decoder of what the YAMLs mean.
- `walk/rulings.yaml` — semantic rulings the YAML cannot carry (nullability,
  extension passthrough, drift resolutions). Emitters in every language honor
  these rows rather than re-deriving the conventions.
- `VERSION` — canonical schema version, dist serial, publish date.
- `MANIFEST.json` — sha256 of every file above.

## How to consume

Pin by tag for humans, **verify by hash for machines** — git tags are mutable,
sha256 is not, so MANIFEST.json is the real pin and the tag is ergonomics.
Fetch your pinned tag, hash-compare against MANIFEST.json in CI, and fail on
mismatch.

**Record the MANIFEST.json hash on your side** — in your lockfile, your CI
config, wherever your pin lives. Comparing a fetched tree against the manifest
that came with it only proves the tree is internally consistent; if the tag
moved, the manifest moved with it and both agree perfectly about the wrong
content. Recording the hash you first accepted is what makes a moved tag
visible. This is the `go.sum` pattern, and it exists because supply-chain
incidents have been built on exactly that gap.

Print the pin's age from `VERSION`'s publish date on every check and warn when
it grows old — **a check that compares you to what you chose can never tell
you your choice went stale.** Warn, never fail: failing on age turns a guard
into a nag, and people delete nags.

## Tags

`v<canonical-version>-dist.<serial>` — e.g. `v0.30.0-dist.1`. The canonical
part tracks the schema standard; the serial increments per publish of the same
canonical version (a presentation fix, a new ruling row). Canonical itself
carries no tags, so this convention starts here rather than inheriting one.

## Vendoring

Copying these files into your own repo is a supported path, not a workaround —
it is what makes an offline or air-gapped build possible, and it is why the
walk is 77 lines with no dependencies. Vendor it, record the hash, and re-run
the check when you update. What is *not* supported is editing your copy: the
walk is the one decoder of what the YAMLs mean, and a forked decoder is how a
standard quietly becomes several standards.

## What this is not

Not a product. No support promise, no compatibility contract beyond the pinned
version, no deprecation ceremony. It is plumbing, and it stays boring — that
is the design, not a stage it will grow out of.
