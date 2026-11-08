# Copyright (c) 2026 Damian Gulczynski.
"""Isolated E57 reader used by Tunnel Studio large-file imports.

Running pye57 in a separate process keeps the desktop/WebView event loops responsive
when a scanner file is several gigabytes or a vendor library takes a long time to
return from a native call.
"""
from pathlib import Path
import json
import sys
import traceback


def emit(kind, value):
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')) if not isinstance(value, str) else value
    sys.stdout.write(f'{kind}\t{payload}\n')
    sys.stdout.flush()


def describe(path):
    import pye57
    result = []
    emit('PROGRESS', 'Opening E57 and reading scan headers…')
    with pye57.E57(str(path)) as source:
        count = source.scan_count
        for i in range(count):
            h = source.get_header(i)
            result.append([i, int(h.point_count)])
            emit('PROGRESS', f'Reading E57 header {i + 1}/{count}…')
    return result


def import_one(path, destination, work, scan_index):
    import pointio
    import jobdata

    def progress(message):
        emit('PROGRESS', str(message))

    index = None if scan_index == 'none' else int(scan_index)
    emit('PROGRESS', 'Opening E57 in isolated import worker…')
    meta = pointio.import_cloud(Path(path), Path(destination), progress, e57_scan=index)
    progress('Building bounded 3D preview cache…')
    jobdata.preview_cache(meta, Path(work), progress)
    return meta


def main():
    if len(sys.argv) < 3:
        raise ValueError('Worker arguments are incomplete.')
    mode = sys.argv[1]
    source = Path(sys.argv[2])
    if not source.is_file():
        raise FileNotFoundError(str(source))
    if mode == 'describe':
        result = describe(source)
    elif mode == 'import':
        if len(sys.argv) != 6:
            raise ValueError('Import worker arguments are incomplete.')
        result = import_one(source, Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
    else:
        raise ValueError('Unknown worker mode.')
    emit('RESULT', result)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit('ERROR', f'{type(exc).__name__}: {exc}')
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
