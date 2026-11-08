"""Read-only RTC360 SCV6 sample inspector, 2026-10-14.

Decodes the observed range/intensity grids and inventories the entire footer.
It does NOT export calibrated XYZ, assemble a point cloud, or register setups.
Dependencies: numpy, Pillow, imagecodecs. Unknown variants fail explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

import numpy as np

MAX_FILE = 256 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_XML = 4 * 1024 * 1024
GRAY_FLOAT_GUID = bytes.fromhex("24c3dd6f034efe4bb1853d77768dc911")
STATUS = "research grids only; calibrated XYZ and measurement validity not verified"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_limited(path, limit=MAX_FILE):
    with Path(path).open("rb") as f:
        data = f.read(limit + 1)
    require(len(data) <= limit, f"File exceeds research reader limit: {Path(path).name}")
    return data


def fingerprint(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "crc32": f"0x{zlib.crc32(data):08x}"}


def read_setup(path):
    """Parse only the supplied setup document; never follow XML file paths."""
    data = read_limited(path, MAX_XML)
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(),
            "XML declarations/entities are not supported")
    root = ET.fromstring(data)
    require(root.tag == "Setup", "Expected setup XML, not a job-level index")
    params = root.find(".//ScanParameters")
    require(params is not None, "Setup has no ScanParameters")
    raster = json.loads(params.attrib["MeasuredRaster"])
    horizontal = int(raster["Horizontal"][1])
    vertical = int(raster["Vertical"][1])
    require(horizontal > 0 and vertical > 0, "Invalid measured raster")
    refs = {}
    for tag in ("ScanSlice", "ImagingSlice", "LwpoSlice", "Thumbnail"):
        records = []
        for item in root.iter(tag):
            ref = dict(item.attrib)
            name = ref.get("filename", "")
            require(name and "/" not in name and "\\" not in name and ":" not in name
                    and name not in (".", ".."), "Unsafe or missing referenced filename")
            records.append(ref)
        refs[tag] = records
    names = [v["filename"] for group in refs.values() for v in group]
    require(len(names) == len(set(names)), "Duplicate setup file reference")
    owner = root.find("OwnerJob")
    kinematics = root.findtext("KinematicParameters")
    tilt = root.findtext("Sensors/Tilt")
    return {"source_name": Path(path).name, **fingerprint(data),
            "name": root.get("Name"), "id": root.get("Id"),
            "owner_job": owner.get("Id") if owner is not None else None,
            "attributes": dict(root.attrib), "scan_parameters": dict(params.attrib),
            "measured_raster": {"rows": horizontal, "columns": vertical},
            "kinematic_parameters_unapplied": json.loads(kinematics) if kinematics else None,
            "tilt_unapplied": json.loads(tilt) if tilt else None,
            "references": refs, "reference_counts": {k: len(v) for k, v in refs.items()}}


def inspect_jxr_header(data, width, height):
    require(len(data) >= 8 and data[:4] == b"II\xbc\x01", "Expected little-endian JPEG XR")
    off = struct.unpack_from("<I", data, 4)[0]
    require(8 <= off <= len(data) - 2, "Invalid JPEG XR IFD offset")
    count = struct.unpack_from("<H", data, off)[0]
    require(0 < count <= 64 and off + 2 + 12 * count + 4 <= len(data), "Invalid JPEG XR IFD")
    fields = {}
    for i in range(count):
        tag, typ, n, value = struct.unpack_from("<HHII", data, off + 2 + 12 * i)
        require(tag not in fields, "Duplicate JPEG XR IFD tag")
        fields[tag] = (typ, n, value)
    require(fields.get(0xBC80) == (4, 1, width)
            and fields.get(0xBC81) == (4, 1, height), "JPEG XR dimensions disagree with scan")
    typ, n, ptr = fields.get(0xBC01, (0, 0, 0))
    require(typ == 1 and n == 16 and ptr + 16 <= len(data)
            and data[ptr:ptr + 16] == GRAY_FLOAT_GUID, "Unsupported JPEG XR pixel format")
    typ, n, start = fields.get(0xBCC0, (0, 0, 0))
    typ2, n2, size = fields.get(0xBCC1, (0, 0, 0))
    require((typ, n, typ2, n2) == (4, 1, 4, 1) and start > 0 and size > 0
            and start + size == len(data), "Invalid JPEG XR codestream extent")
    require(data[start:start + 8] == b"WMPHOTO\0", "Missing JPEG XR codestream signature")
    return {f"0x{tag:04x}": list(val) for tag, val in sorted(fields.items())}


def split_scan(data):
    """Validate framing before calling native image decoders.

    Every byte is accounted for, including all six blocks for every row. Block
    meanings are intentionally left open where only their framing is known.
    """
    require(28 <= len(data) <= MAX_FILE, "Truncated or oversized scan")
    magic, word4, rows, jxr_size, width, height, word24 = struct.unpack_from("<4s6I", data)
    require(magic == b"SCV6", "Unsupported scan signature")
    require(word4 == 6 and word24 == 2162433, "Unsupported SCV6 header variant")
    require(rows == height and 0 < width and 0 < height and width * height <= MAX_PIXELS,
            "Invalid or oversized scan dimensions")
    jp = 28 + jxr_size
    require(jxr_size > 0 and jp + 16 <= len(data), "Invalid JPEG XR length")
    jxr = memoryview(data)[28:jp]
    tags = inspect_jxr_header(jxr, width, height)
    jw, jh, word_jpeg, jpeg_size = struct.unpack_from("<4I", data, jp)
    require((jw, jh, word_jpeg) == (width, height, 0), "Unexpected intensity wrapper")
    start = jp + 16
    pos = start + jpeg_size
    require(jpeg_size > 4 and pos <= len(data), "Invalid JPEG extent")
    require(data[start:start + 2] == b"\xff\xd8" and data[pos - 2:pos] == b"\xff\xd9",
            "Invalid intensity JPEG boundaries")
    jpeg = memoryview(data)[start:pos]
    footer_start = pos
    row_info = []
    previous_index = None
    for row in range(rows):
        blocks, descriptors = [], []
        for block_no in range(6):
            require(pos + 4 <= len(data), f"Missing row {row} block {block_no} length")
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            require(length <= MAX_FILE and pos + length <= len(data),
                    f"Truncated row {row} block {block_no}")
            part = memoryview(data)[pos:pos + length]
            blocks.append(part)
            descriptors.append({"offset": pos, "bytes": length,
                                "sha256": hashlib.sha256(part).hexdigest()})
            pos += length
        require(len(blocks[0]) == 40, "Unexpected row coefficient block")
        index, count, a, b, da, db = struct.unpack("<II4d", blocks[0])
        require(count == width and np.isfinite([a, b, da, db]).all(), "Invalid row coefficients")
        require(previous_index is None or index == previous_index + 1, "Nonconsecutive row indices")
        previous_index = index
        require(len(blocks[1]) == (width + 7) // 8, "Unexpected row bitmap length")
        bits = np.unpackbits(np.frombuffer(blocks[1], np.uint8), bitorder="little")[:width]
        set_bits = int(bits.sum())
        require(set_bits == len(blocks[2]), "Sparse row bytes disagree with little-endian bitmap")
        require(len(blocks[3]) > 0, "Empty opaque row block")
        require(len(blocks[4]) > 0 and len(blocks[4]) % 2 == 0, "Invalid row RLE length")
        rle = np.frombuffer(blocks[4], np.uint8).reshape(-1, 2)
        require(bool((rle[:, 0] > 0).all()) and int(rle[:, 0].sum()) == width,
                "Row RLE does not cover the raster width")
        require(len(blocks[5]) == (width + 1) // 2, "Unexpected packed row block length")
        row_info.append({"index": index, "count": count,
                         "coefficients_unapplied": [a, b, da, db], "sparse_values": set_bits,
                         "rle_covered_columns": int(rle[:, 0].sum()), "blocks": descriptors})
    require(pos == len(data), "Unexpected bytes after the final row")
    report = {"header_unknown_words": {"offset_4": word4, "offset_24": word24},
              "width": width, "height": height, "sample_slots": width * height,
              "jxr_offset": 28, "jxr_bytes": jxr_size, "jxr_ifd": tags,
              "intensity_offset": start, "intensity_bytes": jpeg_size,
              "footer_offset": footer_start, "footer_bytes": len(data) - footer_start,
              "rows": row_info, "all_bytes_accounted_for": True}
    return report, jxr, jpeg


def associate_scan(name, report, setups):
    matches = []
    for setup in setups:
        for ref in setup["references"]["ScanSlice"]:
            if ref["filename"] == name:
                matches.append((setup, ref))
    require(len(matches) == 1, f"Expected one setup reference for {name}; found {len(matches)}")
    setup, ref = matches[0]
    require(ref.get("format") == "21", "Unexpected setup scan format")
    raster = setup["measured_raster"]
    require(report["width"] == raster["columns"]
            and report["rows"][-1]["index"] < raster["rows"], "Scan rows disagree with setup raster")
    return {"name": setup["name"], "id": setup["id"], "reference": ref,
            "setup_slice_count": len(setup["references"]["ScanSlice"]),
            "full_raster_sample_slots": raster["rows"] * raster["columns"]}


def inspect_scan(path, setups, out=None, export_arrays=False):
    import imagecodecs
    from PIL import Image

    data = read_limited(path)
    report, jxr, jpeg = split_scan(data)
    report.update(name=Path(path).name, **fingerprint(data), status=STATUS)
    report["setup"] = associate_scan(Path(path).name, report, setups)
    ranges = imagecodecs.jpegxr_decode(jxr)
    shape = (report["height"], report["width"])
    require(ranges.shape == shape and ranges.dtype == np.float32, "Unexpected decoded range grid")
    with Image.open(io.BytesIO(jpeg)) as im:
        require(im.size == (shape[1], shape[0]) and im.mode == "L", "Unexpected intensity JPEG shape or mode")
        intensity = np.array(im)
    finite = np.isfinite(ranges)
    finite_values = ranges[finite]
    report["decoded_range"] = {
        "dtype": str(ranges.dtype), "finite_samples": int(finite.sum()),
        "positive_finite_samples": int(np.count_nonzero(finite & (ranges > 0))),
        "nonpositive_finite_samples": int(np.count_nonzero(finite & (ranges <= 0))),
        "percentiles_0_1_25_50_75_99_100": np.percentile(finite_values, [0, 1, 25, 50, 75, 99, 100]).tolist()
        if len(finite_values) else None,
        "units": "unverified against an independent measurement reference",
        "full_measurement_precision_verified": False,
        "validity_flags_verified": False,
    }
    report["decoded_intensity"] = {"dtype": str(intensity.dtype), "shape": list(intensity.shape),
        "percentiles_0_1_25_50_75_99_100": np.percentile(intensity, [0, 1, 25, 50, 75, 99, 100]).tolist()}
    if export_arrays:
        require(out is not None, "Export needs a fresh output folder")
        dest = Path(out) / Path(path).stem
        dest.mkdir(exist_ok=False)
        np.save(dest / "range_unvalidated.npy", ranges, allow_pickle=False)
        Image.fromarray(intensity).save(dest / "intensity.png")
        (dest / "footer_uninterpreted.bin").write_bytes(data[report["footer_offset"]:])
    return report


def match_other_sources(paths, setups):
    result = []
    for path in paths:
        refs = [(s, kind, r) for s in setups for kind, group in s["references"].items()
                for r in group if r["filename"] == path.name]
        require(len(refs) == 1, f"Expected one metadata association for {path.name}")
        setup, kind, ref = refs[0]
        data = read_limited(path)
        crc = zlib.crc32(data)
        expected = int(ref["crc32"], 0) if "crc32" in ref else None
        require(expected is None or crc == expected, f"CRC mismatch: {path.name}")
        result.append({"name": path.name, **fingerprint(data), "setup": setup["name"],
                       "setup_id": setup["id"], "kind": kind, "reference": ref,
                       "metadata_crc32_match": crc == expected if expected is not None else None})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scans", nargs="+", type=Path, help="One or more original .scan files")
    parser.add_argument("--setup", action="append", required=True, type=Path,
                        help="Setup-level VGFibGVPZkNvbnRlbnQ= file; repeat for another setup")
    parser.add_argument("--associate", action="append", default=[], type=Path,
                        help="Optional photo/cubemap to match to a setup; repeat as needed")
    parser.add_argument("--output", required=True, type=Path, help="New output folder; existing folders refused")
    parser.add_argument("--export-arrays", action="store_true", help="Save raw range grid, intensity and opaque footer")
    args = parser.parse_args()
    try:
        setups = [read_setup(p) for p in args.setup]
        require(len({s["id"] for s in setups}) == len(setups), "Duplicate setup metadata")
        require(len({p.resolve() for p in args.scans}) == len(args.scans), "Duplicate scan input")
        associations = match_other_sources(args.associate, setups)
        args.output.mkdir(parents=True, exist_ok=False)
        report = {"status": STATUS, "setups": setups, "associated_sources": associations, "scans": []}
        for path in args.scans:
            record = inspect_scan(path, setups, args.output, args.export_arrays)
            report["scans"].append(record)
            print(f"{record['setup']['name']}: decoded {record['sample_slots']:,} sample slots; "
                  f"rows {record['rows'][0]['index']}–{record['rows'][-1]['index']}; XYZ not exported.", flush=True)
        for setup in setups:
            indices = set()
            matched = [r for r in report["scans"] if r["setup"]["id"] == setup["id"]]
            for record in matched:
                current = {r["index"] for r in record["rows"]}
                require(not indices.intersection(current), "Overlapping scan rows in one setup")
                indices.update(current)
            setup["supplied_scan_slices"] = len(matched)
            setup["supplied_rows"] = len(indices)
            setup["complete_raster_supplied"] = indices == set(range(setup["measured_raster"]["rows"]))
        dest = args.output / "inspection.json"
        dest.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Saved {dest}. Research inspection only; source files were not changed.")
    except (OSError, ValueError, ET.ParseError, KeyError, ImportError) as exc:
        print(f"Inspection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
