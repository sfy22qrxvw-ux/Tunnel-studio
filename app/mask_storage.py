# Copyright (c) 2026 Damian Gulczynski.
"""Immutable sparse exclusion masks, compatible with existing byte-mask readers."""
import os
import numpy as np


def _windows_control(stream, code, request=None, output=None):
    import ctypes
    import msvcrt
    returned = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.DeviceIoControl(
        ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno())), code,
        ctypes.byref(request) if request is not None else None,
        ctypes.sizeof(request) if request is not None else 0,
        ctypes.byref(output) if output is not None else None,
        ctypes.sizeof(output) if output is not None else 0,
        ctypes.byref(returned), None)
    return bool(ok), returned.value, ctypes.windll.kernel32.GetLastError()


def _ranges(stream, count):
    if os.name == 'nt':
        import ctypes
        offset = 0
        while offset < count:
            request = (ctypes.c_longlong * 2)(offset, count-offset)
            output = (ctypes.c_longlong * 256)()
            ok, size, error = _windows_control(stream, 0x940CF, request, output)
            if not ok and error != 234:
                yield offset, count
                return
            for index in range(0, size//8, 2):
                start, length = output[index], output[index+1]
                yield start, min(count, start+length)
                offset = start+length
            if ok or not size:return
    else:
        import errno
        offset = 0
        while offset < count:
            try:
                start = os.lseek(stream.fileno(), offset, os.SEEK_DATA)
                stop = os.lseek(stream.fileno(), start, os.SEEK_HOLE)
            except OSError as error:
                if error.errno == errno.ENXIO:return
                yield offset, count
                return
            yield start, min(stop, count)
            offset = stop


def copy_mask(source, destination, count, progress):
    """Copy allocated spans and return their exact number of excluded rows.

    Sparse holes are retained points (zero bytes). Count existing exclusions in
    the blocks already being copied so a legacy edit need not reread its mask.
    The first edit starts with an empty sparse mask and returns zero.
    """
    with open(destination, 'w+b') as out:
        if os.name == 'nt':_windows_control(out, 0x900C4)  # FSCTL_SET_SPARSE
        out.truncate(count)
        progress('Preparing reversible edit · retaining original point data')
        if not source:return 0
        excluded = 0
        with open(source, 'rb') as src:
            # A truncated sparse source may end in a hole. Range enumeration
            # alone would otherwise silently treat its missing tail as valid.
            if os.fstat(src.fileno()).st_size != count:
                raise ValueError('Incomplete exclusion mask.')
            for begin, end in _ranges(src, count):
                for start in range(begin, end, 8*1024*1024):
                    src.seek(start);value=src.read(min(end-start, 8*1024*1024))
                    if len(value)!=min(end-start, 8*1024*1024):raise ValueError('Incomplete exclusion mask.')
                    changed = int(np.count_nonzero(np.frombuffer(value, 'u1')))
                    excluded += changed
                    if changed:out.seek(start);out.write(value)
                    progress(f'Preparing reversible edit · {min(end,start+len(value)):,}/{count:,} mask bytes')
        return excluded
