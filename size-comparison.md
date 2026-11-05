# Comparing sizes of STAC Items and ECMWF index files

## Update, 2026-11-05: one Item per run and vertical domain

The catalog now has **two Items per run** (pressure levels and surface), each
holding every step's GRIB2 file and index file as assets, instead of one Item
per GRIB2 file. Measured on 2026-11-05 against everything then online (12 runs):

| | One Item per file (2026-11-04) | One Item per run and domain (2026-11-05) |
|---|---|---|
| Items | 1,778 (13 runs) | 24 (12 runs) |
| All Item files | ~34 MB | 1.9 MB |
| 00z/12z run, `pl` | 85 × ~11.4 KB ≈ 970 KB | 1 × ~86 KB |
| 00z/12z run, `sfc` | 85 × ~27 KB ≈ 2.3 MB | 1 × ~105 KB |
| 06z/18z run, `pl` / `sfc` | 49 Items each | 1 × ~54 KB / 1 × ~72 KB |

A run's two Items are now much smaller than its index files. A 00z/12z run's
85 `pl` index files total about 2.6 MB, against an 86 KB Item. The variable
descriptions, 64% of the old surface Items, are now written once per run.
Most of a run Item is its assets: two per step, each with a link, title,
`datetime` and `forecast:horizon`.

The rest of this note is the original comparison, for the old layout.

## Original comparison (one Item per GRIB2 file)

Measured on 2026-11-04 against the catalog built from everything then online
(1,778 Items in 13 runs, IFS 0.1° `oper`). The index files are those published
by ECMWF next to each GRIB2 file on data.ecmwf.int.

### Summary

The answer depends on the file type, because the two grow with different
things:

- **An index file** has one JSON line per GRIB message, about 220 bytes each.
- **A STAC Item** has one `cube:variables` entry per parameter. Each pressure
  level is one value of the `pressure_level` dimension, not a separate entry.

| File | GRIB messages / parameters | `.index` | STAC Item | Item ÷ index |
|---|---|---|---|---|
| Pressure levels (`-pl`) | 140 / 10 | ~31 KB | ~11.4 KB | **0.36×** |
| Surface (`-sfc`) | ~51 / 45 (48 at step 0) | ~10.7 KB | ~27 KB | **2.5×** |

- **Pressure-level Items are about a third the size of their index.** Ten
  parameters on 14 levels need 140 index lines but only 10 variable entries.
- **Surface Items are about 2.5× their index.** Nearly every message is a
  different parameter, and each variable entry (name, unit, CF standard name,
  processing comment) is larger than an index line.
- **Both are tiny next to the data.** A pressure-level GRIB2 file is about
  380 MB and a surface file about 94 MB, so Items are roughly 0.003% and 0.03%
  of the file they describe.
- **For the whole catalog,** the 1,778 Items total about 34 MB. The matching
  index files would be about 37 MB.

### Measurements

Item sizes across the whole catalog (889 of each type):

| Type | Items | Average | Min | Max |
|---|---|---|---|---|
| `pl` | 889 | 11,372 B | 11,363 B | 11,375 B |
| `sfc` | 889 | 27,002 B | 26,972 B | 28,195 B |

The largest surface Items are step 0, which has 3 extra fixed fields (`z`,
`sdor`, `slor`).

Individual files from the 2026-11-04 00z run:

| File | `.index` | STAC Item | Item ÷ index |
|---|---|---|---|
| `20261104000000-0h-oper-fc-pl` | 31,043 B | 11,363 B | 0.36 |
| `20261104000000-12h-oper-fc-pl` | 31,186 B | 11,369 B | 0.36 |
| `20261104000000-0h-oper-fc-sfc` | 11,147 B | 28,195 B | 2.52 |
| `20261104000000-12h-oper-fc-sfc` | 10,644 B | 26,987 B | 2.53 |
| `20261104000000-240h-oper-fc-sfc` | 10,694 B | 26,987 B | 2.52 |

Where the bytes go in a surface Item (`20261104000000-12h-oper-fc-sfc.json`):

| Part | Share of the Item |
|---|---|
| `cube:variables` | 64% |
| `cube:dimensions` | 10% |
| `proj:projjson` | 3% |
| everything else (other properties, asset, links) | 23% |

### What each one is for

- **The index** gives the byte offset and length of every GRIB message, so a
  single field can be fetched with an HTTP range request instead of
  downloading the whole file.
- **The STAC Item** describes the file for search and discovery: valid time,
  forecast horizon, grid and projection, and variable names, units and levels.
  It has no byte offsets.

### Possible size reduction

Most of a surface Item's bulk is repeated in every Item: the variable
descriptions are the same across all files apart from a few step-dependent
parameters (`mx2t3`/`mn2t3` up to 144 h, `mx2t6`/`mn2t6` after). The Datacube
fields could be defined once on each run Collection, with each Item listing
only what differs.

### How to reproduce

From this folder, after building the catalog:

```
# Item sizes by type
cd site/stac
for t in pl sfc; do
  find . -name "*-oper-fc-$t.json" -exec stat -f %z {} + |
    awk -v t=$t '{s+=$1; n++} END {printf "%s: %d items, average %.0f bytes\n", t, n, s/n}'
done

# One index file's size
curl -s https://data.ecmwf.int/ecpds/home/opendata/20261104/00z/ifs/0p1/oper/20261104000000-12h-oper-fc-sfc.index | wc -c
```

`stat -f %z` is the macOS form; on Linux use `stat -c %s`.
