# ecmwf-stac: STAC catalog for ECMWF open data (metdx demo)

**Browse:** <https://6a6d74.github.io/ecmwf-stac/browser/>  
**Catalog:** <https://6a6d74.github.io/ecmwf-stac/stac/collection.json>

Generates a static [STAC](https://stacspec.org) catalog, built from STAC
Collections, describing the GRIB2 files of the ECMWF open-data
**IFS 0.1° `oper`** forecasts, and lets you browse it with STAC Browser. It is
published on GitHub Pages and rebuilt hourly when ECMWF's runs change, and can
also be served locally.

The [ECMWF open data collection in the Microsoft Planetary Computer STAC
catalogue](https://planetarycomputer.microsoft.com/api/stac/v1/collections/ecmwf-forecast)
was used as a reference point in creating these STAC resources, including its
`ecmwf:*` property names.

Data: [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data),
licensed [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) under the
[ECMWF Terms of Use](https://apps.ecmwf.int/datasets/licences/general/), © ECMWF,
DOI [10.21957/open-data](https://doi.org/10.21957/open-data).

The catalog is browsed with [STAC Browser](https://github.com/radiantearth/stac-browser)
by the Radiant Earth Foundation and moreGeo GmbH, used unmodified under the
ISC licence. `setup_browser.sh` builds it from the upstream release and ships
its licence with the build as `browser/LICENSE.txt`.

## Quick start

```
cd ~/Development/ecmwf-stac
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# a few files from one run
.venv/bin/python build_catalog.py --date 20261104 --run 00 --steps 0 6 12

# serve on http://127.0.0.1:8000/ (this machine only)
.venv/bin/python serve.py

# once: build STAC Browser into site/browser/ (needs git + Node.js/npm)
./setup_browser.sh   # for local use; see "Publishing on GitHub Pages" for the published site
```

Then open <http://127.0.0.1:8000/browser/>.

## Building the catalog

```
build_catalog.py [--date YYYYMMDD ...] [--run HH ...] [--steps H ...] [--levtype pl|sfc ...]
                 [--root URL] [--out DIR] [--base-url URL] [--cache FILE] [--workers N]
```

| Option | Default | Meaning |
|---|---|---|
| `--date` | all dates online | Only these reference dates |
| `--run` | all runs online | Only these runs, e.g. `00 12` |
| `--steps` | all steps | Only these forecast steps, in hours (fewer assets per Item) |
| `--levtype` | both | Only `pl` (pressure levels) or `sfc` (surface) files |
| `--root` | `https://data.ecmwf.int/ecpds/home/opendata` | Open-data root; asset links point here |
| `--out` | `site/stac` | Output folder; replaced on every run (root is `collection.json`) |
| `--base-url` | `http://127.0.0.1:8000` | Where `site/` is served; used in the local extension's schema URL |
| `--cache` | `.cache/grib-metadata.json` | Parameter and grid metadata cache |
| `--workers` | `16` | Parallel HTTP requests to data.ecmwf.int |

Examples:

```
.venv/bin/python build_catalog.py --date 20261104 --run 00 --steps 0 6 12   # 2 Items, 3 steps each
.venv/bin/python build_catalog.py --date 20261104 --run 00                   # 2 Items, 85 steps each
.venv/bin/python build_catalog.py                                            # everything online
```

ECMWF keeps only the latest runs online, so re-run the script to refresh the
catalog. It rebuilds the whole catalog each time.

### How it works

1. **Discovery:** lists the open-data folders `[yyyymmdd]/[HH]z/ifs/0p1/oper/`
   and selects `*-[step]h-oper-fc-[pl|sfc].grib2`.
2. **Contents:** for each GRIB2 file, reads the small `.index` file ECMWF
   publishes next to it (one JSON line per GRIB message: param, level, byte
   range). Each index file is also an asset of its run's Item (`PT<step>H-index`).
3. **Names, units and grid:** for each parameter not yet in the cache, the
   smallest single message is downloaded by HTTP byte range and decoded with
   ecCodes. That's about 90 MB the first time and nothing afterwards. It reads
   real messages because ecCodes' parameter tables don't always match what is
   published. For example, `tprate` in the files is paramId 260048 "Total
   precipitation rate" (kg m⁻² s⁻¹), not the 172228 time-mean that a table
   lookup returns.
4. **Write:** builds the tree of STAC Collections and Items with pystac,
   using relative links (self-contained).

## Catalog structure

```
site/stac/collection.json                             root Collection: IFS 0.1° oper
└── 20261104/collection.json                          Level 0 Collection: reference date
    └── 12z/collection.json                           Level 1 Collection: reference time (run)
        ├── 20261104120000-oper-fc-pl.json            Item: pressure levels, every step of the run
        └── 20261104120000-oper-fc-sfc.json           Item: surface fields, every step of the run
site/stac/extensions/metdx-demo/v0.1.0/schema.json    local extension schema
```

Each run is described by **two Items, one per vertical domain**: pressure
levels (`pl`) and surface fields (`sfc`). They are kept apart because their
vertical dimensions differ. Each Item holds, for every forecast step of the
run, the GRIB2 file and its index file as assets. So an Item is a Datacube
of time × (level) × latitude × longitude, whose time slices are separate
GRIB2 files.

A root Collection sits above Level 0 so that several dates can be browsed
from one entry point. Each Item belongs to (`collection`) its run Collection.

Every Collection has:

- `extent`: the global bbox, and the span of valid times of the Items below it
- `license` `CC-BY-4.0`, licence links, ECMWF as provider, keywords
- `summaries` of the Item properties below it: `ecmwf:stream`, `ecmwf:type`,
  `ecmwf:resolution`, `metdx-demo:model`, `forecast:perturbed`,
  `ecmwf:reference_datetime`, `forecast:reference_datetime` and
  `metdx-demo:levtype`
- the properties shared by all Items below it, also as top-level fields:
  `ecmwf:stream`, `ecmwf:type`, `ecmwf:resolution`, `metdx-demo:model` and
  `forecast:perturbed` on every Collection; `metdx-demo:reference_date` on
  date and run Collections; `ecmwf:reference_datetime` and
  `forecast:reference_datetime` on run Collections

The root Collection also has the description, DOI and reference links,
including the credit to the Planetary Computer collection.

### Item contents

| Field | Example | Source |
|---|---|---|
| `id` | `20261104120000-oper-fc-sfc` | file name without the step and `.grib2` |
| `datetime` | `null` | an Item covers a time range |
| `start_datetime`, `end_datetime` | `2026-11-04T12:00:00Z`, `2026-11-19T12:00:00Z` | first and last valid times of the run |
| `forecast:reference_datetime`, `ecmwf:reference_datetime` | `2026-11-04T12:00:00Z` | `[yyyymmdd][HH]0000` in the file names |
| `forecast:duration` | `PT0S` (pl only) | see notes |
| `forecast:perturbed` | `false` | `oper` is the control forecast |
| `ecmwf:type`, `ecmwf:stream`, `ecmwf:resolution` | `fc`, `oper`, `0.1` | as in the Planetary Computer items |
| `metdx-demo:model` | `ifs` | `[model]` |
| `metdx-demo:levtype` | `pl` / `sfc` | `-[levtype]` |
| `proj:*` | shape, transform, bbox, PROJJSON | grid keys of real GRIB messages |
| `cube:dimensions` | time (every valid time of the run), latitude, longitude, pressure_level / soil_layer | indexes + GRIB |
| `cube:variables` | one per GRIB `shortName`, with long_name, unit, standard_name, comment | GRIB |

### Assets

Two assets per forecast step, keyed by the step as an ISO 8601 duration:

| Key | Title | Type | Roles |
|---|---|---|---|
| `PT3H` | PT3H step of the IFS 0.1° oper run 2026-11-04 12 UTC | `application/grib;edition=2` | `["data"]` |
| `PT3H-index` | Index for PT3H step of the IFS 0.1° oper run 2026-11-04 12 UTC | `application/x-ndjson` | `["index"]` |

Both carry the step's valid time as `datetime` (e.g. `2026-11-04T15:00:00Z`)
and the step as `ecmwf:step` (`3h`, as in the Planetary Computer items). Asset keys are short identifiers for use in
code (`item.assets["PT3H"]`); the title is what STAC Browser shows.

**Variables present at only some steps** say so in their `comment`, because a
Datacube cannot express this. In the surface files:

- `mx2t3`/`mn2t3` (3-hour max/min) are at +0h to +144h, and `mx2t6`/`mn2t6`
  at +150h to +360h.
- `10fg3` is at +93h to +144h, and `10fg` at the other steps.
- `z`, `sdor` and `slor` are at +0h only.

**Trade-off:** STAC tools generally treat one Item as one moment in time,
with its assets as bands or formats of it. A time search matches a whole run,
and loaders such as `odc-stac` or `stackstac` won't build a time series from
these assets. To get one step, pick the asset by key or by its `datetime`.

### Notes and decisions

- **The file-name timestamp is the reference (run) time, not the valid
  time.** Each asset's `datetime` is reference time + step, as in the
  Planetary Computer items (reference 2026-11-04T00, step 354h → datetime
  2026-11-18T18).
- **The Projection values were checked against the files.** All messages
  are on one grid: regular_ll, 3600 × 1801, 0.1°, first longitude 180° (=
  −180°), sphere R = 6,371,229 m (`shapeOfTheEarth` 6). The transform
  `[0.1, 0, -180.05, 0, -0.1, 90.05]` and bbox `[-180.05, -90.05, 179.95,
  90.05]` describe cell edges, half a cell beyond the grid points. pyproj
  accepts the PROJJSON.
- **Collections, not Catalogs,** are used for every level above the Items.
  Collections can summarise their Items' properties and give their extent,
  and the Forecast extension's v0.2.0 schema covers Items and Collections but
  not Catalogs.
- **`forecast:duration`** is `PT0S` for pressure-level files, where every field
  is instantaneous. Surface files mix instantaneous, accumulated (e.g. `tp`
  since the reference time) and max/min fields (e.g. `mx2t3` over 3 hours),
  so they have no single duration. Each variable's `comment` says how it is
  processed.
- **`ecmwf:*` fields** are not backed by a published extension, as in the
  Planetary Computer catalogue. The `metdx-demo:*` fields have a small local
  extension schema, served at
  `http://127.0.0.1:8000/stac/extensions/metdx-demo/v0.1.0/schema.json`.
- **Index media type:** the `.index` files are JSON Lines. No JSON Lines
  type is registered with IANA, so they use `application/x-ndjson`, the
  de facto type, also used by the Planetary Computer catalogue. ECMWF's server
  sends them as `application/json`, but they aren't a single JSON document;
  `application/json-seq` (RFC 7464) would need an RS character before each
  record.
- **STAC Browser display quirks:** it re-sorts summary lists as text and
  numbers them (`1. 0h`, `2. 102h`, `3. 105h`, …), and shows zero durations
  (`PT0H`, `PT0S`) as "none". Both come from `@radiantearth/stac-fields`;
  the JSON is correct. Forecast steps are no longer summarised in lists, so
  the first quirk no longer affects them.
- **Units** are written UDUNITS-style (`m s**-1` → `m s-1`); `(0 - 1)` and
  `Numeric` become `1`.

## Publishing on GitHub Pages

The workflow `.github/workflows/publish.yml` builds the catalog and STAC
Browser and deploys them to GitHub Pages:

- Catalog: <https://6a6d74.github.io/ecmwf-stac/stac/collection.json>
- Browser: <https://6a6d74.github.io/ecmwf-stac/browser/>

**When it runs:** hourly at :23 and on manual dispatch (Actions → "Build and
publish STAC catalog" → Run workflow; tick *force* to rebuild regardless).
Each run first lists the runs online at ECMWF (`build_catalog.py
--list-runs`) and compares that, plus the repository commit and the STAC
Browser version, with `build-state.txt` on the live site. If nothing has
changed it stops after about a minute; otherwise it rebuilds and deploys
(about 3–4 minutes).

**Controls:**

- Triggers are `schedule` and `workflow_dispatch` only: no pull request,
  issue or comment triggers, so nobody outside the repository can start it.
  Manual runs need write access.
- The workflow token is read-only. Only the deploy job gets `pages: write`
  and `id-token: write`.
- Deploys go through the `github-pages` environment, which only accepts the
  `main` branch.
- Actions are pinned to full commit SHAs, and the repository only allows
  GitHub-owned actions.
- Workflows on pull requests from forks need approval for all outside
  collaborators, in case a `pull_request` trigger is ever added.
- One run at a time (`concurrency: pages`), never cancelling a deploy.

The generated catalog is not committed; each deploy replaces the site. The
parameter/grid metadata cache (`.cache/grib-metadata.json`) is committed as a
seed, and anything learned later is kept between runs with the Actions cache.

On GitHub Pages, `--base-url` is the Pages address, so the local extension
schema is published at
<https://6a6d74.github.io/ecmwf-stac/stac/extensions/metdx-demo/v0.1.0/schema.json>.
`setup_browser.sh` takes the same address as `SITE_URL`.

GitHub disables scheduled workflows in public repositories after 60 days
without repository activity; it emails a warning first, and any commit
re-enables them.

## Serving locally

`serve.py` serves `site/` on **127.0.0.1** only, so other machines can't reach
it. It adds a CORS header so another local STAC client can also read the
catalog.

```
.venv/bin/python serve.py              # http://127.0.0.1:8000/
.venv/bin/python serve.py --port 8080  # also rebuild with --base-url and re-run setup_browser.sh with PORT=8080
```

## Validating

All objects validate against the STAC 1.1.0, Projection v2.0.0, Datacube
v2.2.0, Forecast v0.2.0 and local `metdx-demo` schemas. With the server
running:

```
export SSL_CERT_FILE=$(.venv/bin/python -c "import certifi; print(certifi.where())")
.venv/bin/python -c "import pystac; print(pystac.Collection.from_file('http://127.0.0.1:8000/stac/collection.json').validate_all(), 'items valid')"
```

`SSL_CERT_FILE` is needed with the python.org macOS Python, which ships
without CA certificates.
