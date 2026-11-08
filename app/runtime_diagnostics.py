# Copyright (c) 2026 Damian Gulczynski.
"""Local runtime evidence, including failures hidden by pythonw.exe."""
from datetime import datetime, timezone
from pathlib import Path
import faulthandler
import os
import sys
import threading
import traceback

_stream = None
_path = None
_lock = threading.Lock()


def data_directory():
    return Path(os.environ.get('TUNNEL_STUDIO_DATA', str(
        Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.local/share'))) / 'TunnelStudio')))


def event(message):
    """Diagnostics must never turn a recoverable error into a startup failure."""
    try:
        if _stream is not None:
            with _lock:
                _stream.write(datetime.now(timezone.utc).isoformat() + ' ' + str(message) + '\n')
                _stream.flush()
    except Exception:
        pass


def exception(context, error=None):
    try:
        if error is None:
            detail = traceback.format_exc()
        else:
            detail = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        event(str(context) + '\n' + detail)
    except Exception:
        pass


def install(mode):
    global _stream, _path
    if _stream is not None:
        event('Entry point: ' + str(mode))
        return _path
    try:
        directory = data_directory() / 'diagnostics'
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        path = directory / ('runtime_' + stamp + '_' + str(os.getpid()) + '.log')
        stream = path.open('a', encoding='utf8', buffering=1)
        _stream, _path = stream, path
        from release import VERSION
        event('Tunnel Studio ' + VERSION + ' | ' + str(mode) +
              ' | Python ' + sys.version.replace('\n', ' ') + ' | ' + sys.platform)
        # pythonw has no console streams; keep tracebacks somewhere the user can collect.
        if sys.stderr is None:
            sys.stderr = stream
        if sys.stdout is None:
            sys.stdout = stream
        faulthandler.enable(file=stream, all_threads=True)
        old_hook = sys.excepthook
        def main_hook(kind, error, tb):
            exception('Unhandled main-thread exception', error)
            old_hook(kind, error, tb)
        sys.excepthook = main_hook
        old_thread_hook = threading.excepthook
        def thread_hook(args):
            exception('Unhandled thread exception: ' + str(getattr(args.thread, 'name', 'unknown')), args.exc_value)
            old_thread_hook(args)
        threading.excepthook = thread_hook
        return path
    except Exception:
        return _path
