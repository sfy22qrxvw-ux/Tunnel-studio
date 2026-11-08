"""Read-only research decoder for the RTC360 image samples received 2026-10-14.

This is NOT a point-cloud importer. Observed wrappers only; unknown variants fail.
Dependencies: numpy, Pillow, OpenEXR, imagecodecs, tifffile.
Original files are never changed. Supply a separate --output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zlib

import numpy as np

FACES = ("PosX", "NegX", "PosY", "NegY", "PosZ", "NegZ")
RECORD_PREFIX = b"\x16\x05\x00\x00\x00\x00"
EXR_MAGIC = b"v/1\x01"
MAX_FILE = 512 * 1024 * 1024
MAX_FACE = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_METADATA = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_source(path):
    require(path.stat().st_size <= MAX_FILE, "File exceeds research reader limit")
    return path.read_bytes()


def unwrap_cubemap(data):
    """Recover each named face using the observed power-of-two block framing.

    0x00..0x14: copy 2**tag bytes; 0x15: end of this face.
    These framing bytes also occur INSIDE otherwise standard EXR files. Removing
    only the 11-byte outer prefix is insufficient and produces decode errors.
    """
    pos = 0
    faces = {}
    for _ in range(6):
        require(data[pos:pos + 6] == RECORD_PREFIX, "Unsupported/truncated face record")
        name = data[pos + 6:pos + 10].decode("ascii", errors="strict")
        require(name in FACES and name not in faces, "Unknown or duplicate cube face")
        pos += 10
        parts, size, powers = [], 0, []
        while True:
            require(pos < len(data), "Missing face terminator")
            power = data[pos]
            pos += 1
            if power == 0x15:
                break
            require(power <= 0x14, "Unsupported face block tag")
            length = 1 << power
            size += length
            require(size <= MAX_FACE, "Face exceeds research reader limit")
            require(pos + length <= len(data), "Truncated face block")
            powers.append(power)
            parts.append(data[pos:pos + length])
            pos += length
        faces[name] = (b"".join(parts), powers)
    require(pos == len(data), "Unexpected data after six faces")
    return faces


def inspect_exr(data):
    """Validate the observed standard scanline EXR layout before native decode."""
    require(data[:4] == EXR_MAGIC and len(data) >= 8, "Missing EXR header")
    require(struct.unpack_from("<I", data, 4)[0] == 2, "Unsupported EXR variant")
    pos, attrs = 8, {}
    while True:
        require(pos < min(len(data), 65536), "Invalid EXR header")
        if data[pos] == 0:
            pos += 1
            break
        end = data.find(b"\0", pos, min(len(data), pos + 256))
        require(end >= pos, "Invalid EXR attribute name")
        name = data[pos:end].decode("ascii")
        pos = end + 1
        end = data.find(b"\0", pos, min(len(data), pos + 256))
        require(end >= pos, "Invalid EXR attribute type")
        typ = data[pos:end].decode("ascii")
        pos = end + 1
        require(pos + 4 <= len(data), "Truncated EXR attribute")
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        require(length <= 65536 and pos + length <= len(data), "Invalid EXR attribute length")
        require(name not in attrs, "Duplicate EXR attribute")
        attrs[name] = (typ, data[pos:pos + length])
        pos += length
    require(attrs.get("dataWindow", (None,))[0] == "box2i", "Missing EXR dimensions")
    x0, y0, x1, y1 = struct.unpack("<4i", attrs["dataWindow"][1])
    width, height = x1 - x0 + 1, y1 - y0 + 1
    require(width > 0 and height > 0 and width * height <= MAX_PIXELS, "EXR dimensions exceed limit")
    require(attrs.get("compression") == ("compression", b"\x09"), "Expected observed DWAB compression")
    count = math.ceil(height / 256)
    require(pos + count * 8 <= len(data), "Truncated EXR offset table")
    offsets = struct.unpack_from("<" + "Q" * count, data, pos)
    pos += count * 8
    rows = []
    for index in range(count):
        require(pos + 8 <= len(data), "Truncated EXR chunk")
        require(offsets[index] in (0, pos), "Unexpected EXR chunk offset")
        row, length = struct.unpack_from("<iI", data, pos)
        require(row == y0 + 256 * index, "Unexpected EXR scanline order")
        require(length > 0 and pos + 8 + length <= len(data), "Invalid EXR chunk size")
        rows.append({"row": row, "compressed_bytes": length})
        pos += 8 + length
    # These samples retain trailing bytes after the last EXR chunk. The count
    # is recorded, and bytes are preserved, not interpreted as extra images.
    return {"width": width, "height": height, "compression": "DWAB",
            "chunks": rows, "exr_end": pos, "trailing_bytes": len(data) - pos}


def inspect_cubemap(path, out):
    import OpenEXR
    data = read_source(path)
    result = {"name": path.name, "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "faces": {}}
    for name, (body, powers) in unwrap_cubemap(data).items():
        face = {"unwrapped_bytes": len(body), "block_exponents": powers}
        if body.startswith(EXR_MAGIC):
            face.update(inspect_exr(body))
            dest = out / f"{name}.exr"
            dest.write_bytes(body)
            with OpenEXR.File(str(dest)) as exr:
                require("RGB" in exr.channels(), "Expected RGB image channels")
                pixels = exr.channels()["RGB"].pixels.astype(np.float32)
            require(pixels.shape == (face["height"], face["width"], 3), "Unexpected decoded image dimensions")
            require(bool(np.isfinite(pixels).all()), "Non-finite image values")
            face.update(kind="HDR colour image", decoded_shape=list(pixels.shape),
                        finite_fraction=1.0, minimum=float(pixels.min()), maximum=float(pixels.max()))
        else:
            require(len(body) >= 12, "Truncated auxiliary face")
            width, height, code = struct.unpack_from("<III", body)
            require(0 < width * height <= MAX_PIXELS and width > 0 and height > 0,
                    "Auxiliary dimensions exceed limit")
            require(code == 5 and len(body) - 12 == (width * height + 7) // 8,
                    "Unknown auxiliary encoding")
            bits = np.unpackbits(np.frombuffer(body[12:], np.uint8))[:width * height]
            face.update(kind="one-bit auxiliary layer; semantics unconfirmed", width=width,
                        height=height, type_code=code, set_bits=int(bits.sum()), total_bits=int(bits.size))
        result["faces"][name] = face
    return result


def inspect_img(path, out):
    import imagecodecs
    import tifffile
    data = read_source(path)
    require(len(data) >= 32, "Truncated image wrapper")
    marker, metadata_size = struct.unpack_from("<IQ", data)
    require(marker == 124 and 0 < metadata_size <= MAX_METADATA, "Unknown image wrapper")
    pos = 12 + metadata_size
    require(pos + 20 <= len(data), "Truncated camera metadata block")
    decoder = zlib.decompressobj()
    metadata = decoder.decompress(data[12:pos], MAX_METADATA + 1)
    require(len(metadata) <= MAX_METADATA and decoder.eof and not decoder.unused_data
            and not decoder.unconsumed_tail, "Invalid or oversized compressed metadata")
    marker2, compressed_size, declared_size = struct.unpack_from("<IQQ", data, pos)
    require(marker2 == marker and pos + 20 + compressed_size == len(data), "Invalid image payload length")
    codestream = data[pos + 20:]
    require(codestream[:4] == b"\xff\xd8\xff\xf7" and len(codestream) >= 15,
            "Expected JPEG-LS frame")
    header_len, precision, height, width, components = struct.unpack_from(">HBHHB", codestream, 4)
    require(header_len == 11 and precision == 12 and components == 1,
            "Unsupported JPEG-LS variant")
    require(0 < height * width <= MAX_PIXELS, "JPEG-LS dimensions exceed limit")
    pixels = imagecodecs.jpegls_decode(codestream)
    require(pixels.shape == (height, width) and pixels.dtype == np.uint16,
            "Unexpected decoded JPEG-LS shape")
    start, end = metadata.find(b"<x:xmpmeta"), metadata.find(b"</x:xmpmeta>")
    require(start >= 0 and end > start, "XMP metadata missing")
    xml = metadata[start:end + len(b"</x:xmpmeta>")]
    root = ET.fromstring(xml)
    description = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    require(description is not None, "XMP description missing")
    values = {key.rsplit("}", 1)[-1]: value for key, value in description.attrib.items()}
    with tifffile.TiffFile(io.BytesIO(metadata)) as tif:
        tags = tif.pages[0].tags
        tiff_width, tiff_height = int(tags[256].value), int(tags[257].value)
        black = tags[50714].value
        black_level = float(black[0] / black[1])
    (out / "camera_metadata.xmp.xml").write_bytes(xml)
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "kind": "raw camera image with TIFF/XMP metadata and JPEG-LS pixels",
            "wrapper_marker": marker, "compressed_metadata_bytes": metadata_size,
            "metadata_bytes": len(metadata), "jpeg_ls_bytes": compressed_size,
            "declared_uncompressed_bytes": declared_size, "jpeg_ls_precision": precision,
            "decoded_shape": list(pixels.shape), "decoded_bytes": int(pixels.nbytes),
            "decoded_range": [int(pixels.min()), int(pixels.max())],
            "tiff_dimensions": [tiff_width, tiff_height], "black_level": black_level,
            "capture_fields": {key: values.get(key) for key in
                ("CamId", "FrameId", "PosId", "BurstID", "BurstLength", "ExposureTime", "Hz")},
            "pixel_layout_verified": False,
            "limitation": "Decoded JPEG-LS dimensions differ from TIFF raster dimensions; pixel arrangement and demosaicing are not established. No calibrated RGB frame or 3D coordinates produced."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # A fresh output folder avoids overwriting sources or earlier investigations.
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"scope": "Research decoder; no measured point-cloud import or application integration", "files": []}
    failed = False
    for index, source in enumerate(args.files):
        dest = args.output / f"sample_{index + 1}"
        dest.mkdir()
        try:
            if source.suffix.lower() == ".cubemap":
                item = inspect_cubemap(source, dest)
            elif source.suffix.lower() == ".img":
                item = inspect_img(source, dest)
            else:
                raise ValueError("Only observed .cubemap and .img samples are supported")
            item["status"] = "inspected"
        except Exception as error:
            failed = True
            item = {"name": source.name, "status": "error", "error": f"{type(error).__name__}: {error}"}
        report["files"].append(item)
        print(source.name, item["status"])
    (args.output / "inspection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
