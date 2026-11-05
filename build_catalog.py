#!/usr/bin/env python3
"""Build a static STAC catalog for ECMWF open data (IFS, 0.1°, oper).

Walks the ECMWF open-data folder structure

    [ROOT]/[yyyymmdd]/[HH]z/ifs/0p1/oper/[yyyymmdd][HH]0000-[step]h-oper-fc-[levtype].grib2

and writes one STAC Item per GRIB2 file, organised as

    collection.json                    root Collection: IFS 0.1° oper
    └── 20261104/collection.json       Level 0 Collection: forecast reference date
        └── 00z/collection.json        Level 1 Collection: forecast reference time
            └── <file-id>.json         Level 2: one Item per GRIB2 file

What is inside each GRIB2 file (parameters, levels) is read from the small
.index file that ECMWF publishes next to it, which is also added to each Item
as an asset with role "index". Parameter names/units and the grid geometry are read from real
GRIB messages: for each parameter the smallest single message is downloaded
by HTTP byte range, decoded with ecCodes, and cached in --cache so later runs
download nothing.

The structure of the ECMWF open-data collection in the Microsoft Planetary
Computer STAC catalogue was used as a reference point.

Examples:
    python build_catalog.py --date 20261104 --run 00 --steps 0 6 12   # a few files
    python build_catalog.py --date 20261104 --run 00                   # a whole run
    python build_catalog.py                                            # everything online
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import re
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

import pystac

DEFAULT_ROOT = "https://data.ecmwf.int/ecpds/home/opendata"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
MODEL, RESOL, STREAM, TYPE = "ifs", "0p1", "oper", "fc"
RESOLUTION_DEG = "0.1"
USER_AGENT = "ecmwf-stac-demo/0.1 (static STAC catalog builder)"

GRIB2_MEDIA_TYPE = "application/grib;edition=2"
# The .index files are JSON Lines (one JSON object per line). No JSON Lines media
# type is registered with IANA; application/x-ndjson is the de facto type, also
# used by the Planetary Computer catalogue. (ECMWF serves them as application/json,
# but they are not a single JSON document; application/json-seq would need an
# RS character before each record, which these files don't have.)
INDEX_MEDIA_TYPE = "application/x-ndjson"
INDEX_ASSET_DESCRIPTION = (
    "ECMWF index of the GRIB2 file: one JSON object per line, one line per GRIB "
    "message, giving the message's MARS keys (param, levelist, step, ...) and its "
    "byte range in the GRIB2 file (_offset, _length).")
EXT = {
    "proj": "https://stac-extensions.github.io/projection/v2.0.0/schema.json",
    "cube": "https://stac-extensions.github.io/datacube/v2.2.0/schema.json",
    "forecast": "https://stac-extensions.github.io/forecast/v0.2.0/schema.json",
}
LOCAL_EXT_PATH = "stac/extensions/metdx-demo/v0.1.0/schema.json"

FILE_RE = re.compile(
    r"^(?P<ref>\d{14})-(?P<step>\d+)(?P<unit>[hm])-(?P<stream>[a-z]+)-(?P<type>[a-z]+)"
    r"(?:-(?P<levtype>[a-z]+))?\.grib2$")

# --- Descriptive text, from ECMWF's open-data pages ---------------------------
ECMWF_OPEN_DATA_PAGE = "https://www.ecmwf.int/en/forecasts/datasets/open-data"
ECMWF_DOCS_PAGE = ("https://confluence.ecmwf.int/display/DAC/"
                   "ECMWF+open+data%3A+real-time+forecasts+from+IFS+and+AIFS")
ECMWF_TERMS = "https://apps.ecmwf.int/datasets/licences/general/"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
DOI = "https://doi.org/10.21957/open-data"
MPC_COLLECTION = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/ecmwf-forecast"

STREAM_TITLE = "IFS medium-range control forecast, atmospheric (formerly HRES)"
LEVTYPE_TITLE = {"pl": "pressure levels", "sfc": "surface and single levels"}

ROOT_DESCRIPTION = f"""\
Static STAC catalog of the **ECMWF open data** real-time forecasts from the
**IFS** model at **0.1°** resolution, stream **oper** ({STREAM_TITLE}).

From the [ECMWF open-data page]({ECMWF_OPEN_DATA_PAGE}): *"A subset of ECMWF
real-time forecast data from the IFS and AIFS models is made available to the
public free of charge."* The 0.1° IFS data, available from 20 October 2026, are
produced from the same ~9 km high-resolution forecast as the 0.25° data. There
are four runs a day (00, 06, 12 and 18 UTC), and at 0.1° surface (`-sfc`) and
pressure-level (`-pl`) fields are published as separate files. ECMWF keeps only
the most recent forecast runs online, so this catalog is regenerated from the
live folder structure. See the [ECMWF open-data documentation]({ECMWF_DOCS_PAGE}).

Organisation: Collections per forecast reference date → reference time (run)
→ one Item per GRIB2 file. Each Item describes its horizontal grid with the Projection
extension, its contents (variables, levels, time) with the Datacube extension,
and its reference time and lead time with the Forecast extension.

Licence: [CC-BY-4.0]({CC_BY}), subject to the [ECMWF Terms of Use]({ECMWF_TERMS}).
Data © ECMWF. DOI: [10.21957/open-data]({DOI}).

*The [ECMWF open data collection in the Microsoft Planetary Computer STAC
catalogue]({MPC_COLLECTION}) was used as a reference point in creating these
STAC resources, including its `ecmwf:*` property names.*
"""


# --- HTTP ---------------------------------------------------------------------
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def http_get(url, byte_range=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if byte_range:
        req.add_header("Range", f"bytes={byte_range[0]}-{byte_range[1]}")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


def list_dir(url):
    """Names of the entries linked from an open-data folder listing page."""
    html = http_get(url.rstrip("/") + "/").decode("utf-8", "replace")
    names = []
    for href in re.findall(r'href="([^"]+)"', html):
        if href.startswith(("#", "http", "javascript")) or "'" in href:
            continue
        name = href.rstrip("/").rsplit("/", 1)[-1]
        if name and name not in names:
            names.append(name + ("/" if href.endswith("/") else ""))
    return names


# --- GRIB metadata from real messages -------------------------------------------
GRID_KEYS = ["gridType", "shapeOfTheEarth", "radius", "Ni", "Nj",
             "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
             "latitudeOfLastGridPointInDegrees", "longitudeOfLastGridPointInDegrees",
             "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
             "jScansPositively"]
PARAM_KEYS = ["shortName", "paramId", "name", "units", "cfName", "stepType",
              "typeOfLevel", "level", "stepRange"]


def decode_message(data):
    import eccodes
    h = eccodes.codes_new_from_message(data)
    try:
        get = lambda k: eccodes.codes_get(h, k)  # noqa: E731
        return {"param": {k: get(k) for k in PARAM_KEYS},
                "grid": {k: get(k) for k in GRID_KEYS}}
    finally:
        eccodes.codes_release(h)


def cache_key(file_levtype, row):
    return f"{file_levtype}/{row.get('levtype')}/{row['param']}"


# --- Helpers --------------------------------------------------------------------
def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def udunits(units):
    """ecCodes units -> UDUNITS-style strings for the Datacube 'unit' field."""
    if units in ("(0 - 1)", "~", "Numeric"):
        return "1", None
    if units.startswith("(Code table"):
        return None, f"Values are codes from GRIB2 {units.strip('()').lower()}."
    if units == "m of water equivalent":
        return "m", "Metres of water equivalent."
    return units.replace("**", ""), None


def level_sort_key(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("inf")


def grid_projection(grid):
    """Projection-extension fields for a regular lat/lon grid on a sphere."""
    if grid["gridType"] != "regular_ll":
        raise SystemExit(f"unexpected gridType {grid['gridType']}")
    if grid["shapeOfTheEarth"] != 6:
        raise SystemExit(f"unexpected shapeOfTheEarth {grid['shapeOfTheEarth']}")
    r = int(grid["radius"])
    nx, ny = grid["Ni"], grid["Nj"]
    dx, dy = grid["iDirectionIncrementInDegrees"], grid["jDirectionIncrementInDegrees"]
    lon0 = grid["longitudeOfFirstGridPointInDegrees"]
    lon0 = lon0 - 360 if lon0 >= 180 else lon0          # 180° is the same meridian as -180°
    lat0 = grid["latitudeOfFirstGridPointInDegrees"]
    if grid["jScansPositively"]:
        raise SystemExit("expected rows to run north to south")
    rnd = lambda v: round(v, 6)  # noqa: E731
    x0, y0 = rnd(lon0 - dx / 2), rnd(lat0 + dy / 2)      # outer edge of the first cell
    projjson = {
        "$schema": "https://proj.org/schemas/v0.7/projjson.schema.json",
        "type": "GeographicCRS",
        "name": f"Geographic CRS on sphere R={r} m (GRIB2 shapeOfTheEarth=6)",
        "datum": {
            "type": "GeodeticReferenceFrame",
            "name": f"Unknown datum based upon sphere R={r} m",
            "ellipsoid": {"name": f"Sphere R={r} m", "radius": r},
        },
        "coordinate_system": {
            "subtype": "ellipsoidal",
            "axis": [
                {"name": "Longitude", "abbreviation": "lon", "direction": "east", "unit": "degree"},
                {"name": "Latitude", "abbreviation": "lat", "direction": "north", "unit": "degree"},
            ],
        },
    }
    proj = {
        "proj:code": None,
        "proj:projjson": projjson,
        "proj:shape": [ny, nx],
        "proj:transform": [dx, 0.0, x0, 0.0, -dy, y0, 0.0, 0.0, 1.0],
        "proj:bbox": [x0, rnd(y0 - ny * dy), rnd(x0 + nx * dx), y0],
    }
    lon_extent = [rnd(lon0), rnd(lon0 + (nx - 1) * dx)]
    lat_extent = sorted([rnd(lat0), rnd(lat0 - (ny - 1) * dy)])
    return proj, projjson, lon_extent, lat_extent, dx, dy


def describe_variable(meta, dims, levels_note):
    p = meta["param"]
    unit, unit_note = udunits(p["units"])
    stat = {"accum": "Accumulated from the forecast reference time to the valid time.",
            "max": "Maximum over a period ending at the valid time.",
            "min": "Minimum over a period ending at the valid time.",
            "avg": "Average over a period ending at the valid time."}.get(p["stepType"])
    comment = " ".join(x for x in [levels_note, stat, unit_note] if x)
    v = {
        "dimensions": dims,
        "type": "data",
        "description": f"{p['name']} (GRIB shortName {p['shortName']}, ECMWF paramId {p['paramId']}).",
        "long_name": p["name"],
        "short_name": p["shortName"],
    }
    if unit:
        v["unit"] = unit
    if p.get("cfName") and p["cfName"] != "unknown":
        v["standard_name"] = p["cfName"]
    if comment:
        v["comment"] = comment
    return v


# --- Build ----------------------------------------------------------------------
def discover(root, dates, runs):
    """Yield (date, HH, [filenames]) for IFS 0.1° oper runs present online."""
    online_dates = sorted(n.rstrip("/") for n in list_dir(root) if re.fullmatch(r"\d{8}/", n))
    for d in online_dates:
        if dates and d not in dates:
            continue
        online_runs = sorted(n[:2] for n in list_dir(f"{root}/{d}") if re.fullmatch(r"\d{2}z/", n))
        for hh in online_runs:
            if runs and hh not in runs:
                continue
            folder = f"{root}/{d}/{hh}z/{MODEL}/{RESOL}/{STREAM}"
            try:
                names = [n for n in list_dir(folder) if n.endswith(".grib2")]
            except Exception as e:  # run not (yet) published for this model/resol/stream
                print(f"  skip {d} {hh}z: {e}", file=sys.stderr)
                continue
            yield d, hh, folder, names


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="\n".join(__doc__.split("\n")[1:]))
    p.add_argument("--root", default=DEFAULT_ROOT, help=f"open-data root URL (default {DEFAULT_ROOT})")
    p.add_argument("--date", nargs="+", metavar="YYYYMMDD", help="only these reference dates")
    p.add_argument("--run", nargs="+", metavar="HH", help="only these runs, e.g. 00 12")
    p.add_argument("--steps", nargs="+", type=int, metavar="H", help="only these steps (hours)")
    p.add_argument("--levtype", nargs="+", choices=["pl", "sfc"], help="only these file types")
    p.add_argument("--out", default="site/stac", help="output folder (default site/stac)")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help="URL the site folder is served at; used for the local extension "
                        f"schema URL (default {DEFAULT_BASE_URL})")
    p.add_argument("--cache", default=".cache/grib-metadata.json",
                   help="parameter/grid metadata cache (default .cache/grib-metadata.json)")
    p.add_argument("--workers", type=int, default=16, help="parallel HTTP requests (default 16)")
    p.add_argument("--list-runs", action="store_true",
                   help="only print the matching runs online, one 'YYYYMMDD HHz N-files' line "
                        "each, and exit (used to detect changes before rebuilding)")
    args = p.parse_args()

    root = args.root.rstrip("/")
    out = Path(args.out)
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    local_ext = f"{args.base_url.rstrip('/')}/{LOCAL_EXT_PATH}"

    # 1. Discover files
    if not args.list_runs:
        print(f"Listing {root} ...")
    plan = []  # (date, hh, folder, filename, match)
    for d, hh, folder, names in discover(root, args.date, args.run):
        for n in sorted(names):
            m = FILE_RE.match(n)
            if not m or m["stream"] != STREAM or m["type"] != TYPE or m["unit"] != "h":
                continue
            if args.steps is not None and int(m["step"]) not in args.steps:
                continue
            if args.levtype and m["levtype"] not in args.levtype:
                continue
            plan.append((d, hh, folder, n, m))
    runs_listing = "".join(
        f"{d} {hh}z {n}\n" for (d, hh), n in
        sorted(collections.Counter((d, hh) for d, hh, *_ in plan).items()))
    if args.list_runs:
        print(runs_listing, end="")
        return
    if not plan:
        raise SystemExit("no matching GRIB2 files found")
    print(f"  {len(plan)} GRIB2 files in "
          f"{len({(d, hh) for d, hh, *_ in plan})} run(s)")

    # 2. Fetch every file's index
    def fetch_index(entry):
        folder, n = entry[2], entry[3]
        raw = http_get(f"{folder}/{n[:-len('.grib2')]}.index").decode()
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    print("Reading .index files ...")
    with cf.ThreadPoolExecutor(args.workers) as ex:
        indexes = list(ex.map(fetch_index, plan))

    # 3. Sample one real message per (file levtype, levtype, param) not yet cached
    wanted = {}
    for entry, rows in zip(plan, indexes):
        url, lt = f"{entry[2]}/{entry[3]}", entry[4]["levtype"]
        for row in rows:
            k = cache_key(lt, row)
            if k not in cache and (k not in wanted or row["_length"] < wanted[k][1]["_length"]):
                wanted[k] = (url, row)
    if wanted:
        mb = sum(r["_length"] for _, r in wanted.values()) / 1e6
        print(f"Sampling {len(wanted)} parameters from real GRIB messages ({mb:.0f} MB) ...")

        def sample(item):
            k, (url, row) = item
            o, n = row["_offset"], row["_length"]
            return k, decode_message(http_get(url, (o, o + n - 1)))

        with cf.ThreadPoolExecutor(args.workers) as ex:
            for k, meta in ex.map(sample, wanted.items()):
                cache[k] = meta
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))

    # 4. Build the Items, grouped by run
    # Properties shared by every Item; also set on each Collection above them.
    common = {"ecmwf:stream": STREAM, "ecmwf:type": TYPE, "ecmwf:resolution": RESOLUTION_DEG,
              "metdx-demo:model": MODEL, "forecast:perturbed": False}
    runs = {}  # (date, HH) -> (reference time, [items])
    for (d, hh, folder, n, m), rows in zip(plan, indexes):
        ref = dt.datetime.strptime(m["ref"], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
        runs.setdefault((d, hh), (ref, []))[1].append(
            make_item(folder, n, m, ref, rows, cache, common, local_ext))

    # 5. Collections: root -> reference date -> reference time (run) -> Items
    all_items = [it for _, items in runs.values() for it in items]
    root = make_collection(
        f"ecmwf-opendata-{MODEL}-{RESOL}-{STREAM}",
        f"ECMWF open data: IFS 0.1° {STREAM} ({STREAM_TITLE})",
        ROOT_DESCRIPTION, all_items, common, local_ext)
    add_reference_links(root)
    hrefs = [(root, out / "collection.json")]
    for d in sorted({d for d, _ in runs}):
        day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        day_runs = sorted((k, v) for k, v in runs.items() if k[0] == d)
        date_col = make_collection(
            d, f"Forecast runs with reference date {day}",
            (f"IFS 0.1° {STREAM} forecast runs whose reference (base) date is {day}, "
             f"one collection per reference time (00/06/12/18 UTC)."),
            [it for _, (_, items) in day_runs for it in items],
            {**common, "metdx-demo:reference_date": day}, local_ext)
        add_license_links(date_col)
        root.add_child(date_col)
        hrefs.append((date_col, out / d / "collection.json"))
        for (_, hh), (ref, items) in day_runs:
            run_col = make_collection(
                f"{d}-{hh}z", f"IFS 0.1° {STREAM} run {day} {hh} UTC",
                (f"GRIB2 files of the IFS 0.1° {STREAM} forecast with reference time "
                 f"{iso(ref)}: one Item per file and forecast step, separate files for "
                 f"pressure-level (pl) and surface (sfc) fields."),
                items,
                {**common, "metdx-demo:reference_date": day,
                 "ecmwf:reference_datetime": iso(ref), "forecast:reference_datetime": iso(ref)},
                local_ext)
            add_license_links(run_col)
            date_col.add_child(run_col)
            hrefs.append((run_col, out / d / f"{hh}z" / "collection.json"))
            for it in sorted(items, key=lambda i: (i.datetime, i.id)):
                run_col.add_item(it)
                it.set_self_href(str(out / d / f"{hh}z" / f"{it.id}.json"))

    # 6. Write
    if out.exists():
        if not any((out / f).exists() for f in ("collection.json", "catalog.json")):
            raise SystemExit(f"{out} exists but is not a STAC folder; not overwriting it")
        shutil.rmtree(out)
    for obj, href in hrefs:
        obj.set_self_href(str(href))
    root.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
    write_local_extension(out.parent / LOCAL_EXT_PATH, local_ext)
    (out / "runs.txt").write_text(runs_listing)  # what this build covered; see --list-runs
    print(f"Wrote {len(all_items)} Items in {len(runs)} run(s) to {out}/collection.json")


def make_collection(cid, title, description, items, fields, local_ext):
    """A Collection whose extent and summaries come from the Items below it.

    `fields` are properties shared by all those Items; they are set as
    top-level Collection fields and also appear in `summaries`.
    """
    times = [it.datetime for it in items]
    steps = sorted({it.properties["ecmwf:step"] for it in items}, key=lambda s: int(s[:-1]))
    summaries = {k: [v] for k, v in fields.items() if k != "metdx-demo:reference_date"}
    summaries.update({
        "metdx-demo:levtype": sorted({it.properties["metdx-demo:levtype"] for it in items}),
        "ecmwf:step": steps,
        "forecast:horizon": [f"PT{s[:-1]}H" for s in steps],
        "forecast:reference_datetime": sorted({it.properties["forecast:reference_datetime"]
                                               for it in items}),
    })
    return pystac.Collection(
        id=cid, title=title, description=description,
        extent=pystac.Extent(pystac.SpatialExtent([[-180.0, -90.0, 180.0, 90.0]]),
                             pystac.TemporalExtent([[min(times), max(times)]])),
        license="CC-BY-4.0",
        keywords=["ECMWF", "IFS", "open data", "forecast", "weather", "GRIB2"],
        providers=[pystac.Provider(
            "ECMWF", "European Centre for Medium-Range Weather Forecasts",
            roles=[pystac.ProviderRole.PRODUCER, pystac.ProviderRole.LICENSOR,
                   pystac.ProviderRole.HOST],
            url="https://www.ecmwf.int/")],
        summaries=pystac.Summaries(summaries, maxcount=10000),  # pystac drops lists > 25 by default
        stac_extensions=[EXT["forecast"], local_ext],
        extra_fields={
            **fields,
            "item_assets": {
                "data": {
                    "type": GRIB2_MEDIA_TYPE, "roles": ["data"], "title": "GRIB2 data file",
                    "description": "The forecast fields of one model run and step, as GRIB2."},
                "index": {
                    "type": INDEX_MEDIA_TYPE, "roles": ["index"], "title": "Index file",
                    "description": INDEX_ASSET_DESCRIPTION},
            },
        },
        catalog_type=pystac.CatalogType.SELF_CONTAINED,
    )


def add_license_links(obj):
    obj.add_link(pystac.Link("license", CC_BY, "text/html", "CC-BY-4.0"))
    obj.add_link(pystac.Link("license", ECMWF_TERMS, "text/html", "ECMWF Terms of Use"))


def add_reference_links(obj):
    add_license_links(obj)
    obj.add_link(pystac.Link("describedby", ECMWF_OPEN_DATA_PAGE, "text/html", "ECMWF open data"))
    obj.add_link(pystac.Link("describedby", ECMWF_DOCS_PAGE, "text/html",
                             "ECMWF open data documentation"))
    obj.add_link(pystac.Link("cite-as", DOI, "text/html", "DOI 10.21957/open-data"))
    obj.add_link(pystac.Link("related", MPC_COLLECTION, "application/json",
                             "Reference: ECMWF open data collection in the Microsoft Planetary "
                             "Computer STAC catalogue"))


def make_item(folder, name, m, ref, rows, cache, common, local_ext):
    step, lt = int(m["step"]), m["levtype"]
    valid = ref + dt.timedelta(hours=step)
    item_id = name[:-len(".grib2")]

    # Grid from the sampled messages of this file type (all must agree)
    grids = {json.dumps(cache[cache_key(lt, r)]["grid"], sort_keys=True) for r in rows}
    if len(grids) != 1:
        raise SystemExit(f"{name}: messages on {len(grids)} different grids")
    proj, projjson, lon_ext, lat_ext, dx, dy = grid_projection(json.loads(grids.pop()))

    # Contents from the index: variables and their levels
    levels = {}
    for r in rows:
        levels.setdefault((r.get("levtype"), r["param"]), set()).add(r.get("levelist"))
    dims = {
        "time": {"type": "temporal", "extent": [iso(valid), iso(valid)], "values": [iso(valid)],
                 "description": "Valid time (reference time + forecast step)."},
        "latitude": {"type": "spatial", "axis": "y", "extent": lat_ext, "step": dy,
                     "reference_system": projjson,
                     "description": f"Latitude, {dy}° spacing; rows run north to south."},
        "longitude": {"type": "spatial", "axis": "x", "extent": lon_ext, "step": dx,
                      "reference_system": projjson,
                      "description": (f"Longitude, {dx}° spacing, from the 180° meridian eastwards "
                                      "(the GRIB header gives the first longitude as 180°).")},
    }
    variables = {}
    for (levtype, param), lv in levels.items():
        meta = cache[cache_key(lt, {"levtype": levtype, "param": param})]
        p = meta["param"]
        if levtype == "pl":
            values = sorted((int(x) for x in lv), reverse=True)
            dims["pressure_level"] = {"type": "spatial", "axis": "z", "unit": "hPa",
                                      "extent": [min(values), max(values)], "values": values,
                                      "description": "Isobaric (pressure) levels."}
            vdims, note = ["time", "pressure_level", "latitude", "longitude"], None
        elif levtype == "sol":
            values = sorted(int(x) for x in lv)
            dims["soil_layer"] = {"type": "other", "extent": [min(values), max(values)],
                                  "values": values,
                                  "description": "IFS soil layer number, 1 = top layer."}
            vdims, note = ["time", "soil_layer", "latitude", "longitude"], None
        else:
            vdims = ["time", "latitude", "longitude"]
            lvl = f" {p['level']}" if p["typeOfLevel"] == "heightAboveGround" else ""
            unit = " m" if lvl else ""
            note = f"Level type: {p['typeOfLevel']}{lvl}{unit}."
        variables[param] = describe_variable(meta, vdims, note)

    props = {
        "title": (f"IFS 0.1° {STREAM} {TYPE}, {LEVTYPE_TITLE.get(lt, lt)}, "
                  f"{iso(ref)[:13].replace('T', ' ')} UTC +{step}h"),
        "description": (f"{STREAM_TITLE}. {len(variables)} parameters "
                        f"({LEVTYPE_TITLE.get(lt, lt)}) in {len(rows)} GRIB2 messages, "
                        f"reference time {iso(ref)}, step {step} h, valid at {iso(valid)}."),
        **common,
        "ecmwf:reference_datetime": iso(ref),
        "ecmwf:forecast_datetime": iso(valid),
        "ecmwf:step": f"{step}h",
        "metdx-demo:levtype": lt,
        "forecast:reference_datetime": iso(ref),
        "forecast:horizon": f"PT{step}H",
        "forecast:perturbed": False,
        **proj,
        "cube:dimensions": dims,
        "cube:variables": dict(sorted(variables.items())),
    }
    # Every pressure-level field is instantaneous; surface files mix instantaneous,
    # accumulated and max/min fields, so no single forecast:duration applies.
    if all(cache[cache_key(lt, r)]["param"]["stepType"] == "instant" for r in rows):
        props["forecast:duration"] = "PT0S"

    item = pystac.Item(
        id=item_id,
        geometry={"type": "Polygon", "coordinates": [[[-180, -90], [180, -90], [180, 90],
                                                      [-180, 90], [-180, -90]]]},
        bbox=[-180.0, -90.0, 180.0, 90.0],
        datetime=valid,
        properties=props,
        stac_extensions=[EXT["proj"], EXT["cube"], EXT["forecast"], local_ext],
    )
    item.add_asset("data", pystac.Asset(
        href=f"{folder}/{name}", media_type=GRIB2_MEDIA_TYPE, roles=["data"],
        title="GRIB2 data file",
        description=(f"{len(rows)} GRIB2 messages, {sum(r['_length'] for r in rows):,} bytes. "
                     "Single fields can be downloaded with HTTP range requests using the "
                     "byte offsets in the index asset.")))
    item.add_asset("index", pystac.Asset(
        href=f"{folder}/{name[:-len('.grib2')]}.index", media_type=INDEX_MEDIA_TYPE,
        roles=["index"], title="Index file", description=INDEX_ASSET_DESCRIPTION))
    item.add_link(pystac.Link("license", CC_BY, "text/html", "CC-BY-4.0"))
    item.add_link(pystac.Link("describedby", ECMWF_OPEN_DATA_PAGE, "text/html", "ECMWF open data"))
    return item


def write_local_extension(path, url):
    """JSON Schema for the demo's own fields (metdx-demo:*)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "metdx-demo:model": {"type": "string", "description":
                             "Production model, the [model] part of the open-data path, e.g. ifs."},
        "metdx-demo:levtype": {"type": "string", "enum": ["pl", "sfc"], "description":
                               "Level type of a 0.1° file, the -[levtype] filename suffix."},
        "metdx-demo:reference_date": {"type": "string", "format": "date", "description":
                                      "Forecast reference (base) date, the [yyyymmdd] folder."},
    }
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": url,
        "title": "metdx-demo local extension",
        "description": "Fields used by the metdx demo STAC catalog of ECMWF open data.",
        "type": "object",
        "required": ["stac_extensions"],
        "properties": {"stac_extensions": {"type": "array", "contains": {"const": url}}},
        "oneOf": [
            {"$comment": "Item", "required": ["type", "properties"],
             "properties": {"type": {"const": "Feature"},
                            "properties": {"type": "object", "properties": fields}}},
            {"$comment": "Catalog or Collection",
             "properties": {"type": {"enum": ["Catalog", "Collection"]}, **fields}},
        ],
    }
    path.write_text(json.dumps(schema, indent=2))


if __name__ == "__main__":
    main()
