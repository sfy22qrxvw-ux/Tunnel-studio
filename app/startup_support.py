# Copyright (c) 2026 Damian Gulczynski.
"""Desktop startup helpers. Importable without third-party packages."""
import ctypes
import hashlib
from importlib import import_module
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

APP = Path(__file__).resolve().parent


def data_folder():
    return Path(os.environ.get('TUNNEL_STUDIO_DATA', str(
        Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'TunnelStudio')))


def reveal_data_folder(work):
    """Open only the backend's active workspace directory in Windows Explorer."""
    folder = Path(work).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('The active project data folder is unavailable.')
    if not hasattr(os, 'startfile'):
        raise ValueError('Open this path in your file manager: ' + str(folder))
    os.startfile(str(folder), 'explore')
    return {'opened': True}


def configure_bridge():
    if sys.platform != 'win32':
        return
    dll = Path(sys.base_prefix) / f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    if not dll.is_file():
        raise RuntimeError(f'The bundled Python runtime DLL is missing: {dll}')
    os.environ['PYTHONNET_PYDLL'] = str(dll)
    from pythonnet import load
    load('netfx')


def desktop_backend():
    configure_bridge()
    import webview
    if sys.platform == 'win32':
        # pywebview also has a package-level `guilib = None` until start().
        # Resolve the actual module without reading or replacing that attribute.
        guilib_module = import_module('webview.guilib')
        guilib_module.forced_gui_ = 'edgechromium'
        from webview.platforms import winforms
        if winforms.renderer != 'edgechromium':
            raise RuntimeError('The desktop bridge loaded, but a usable Microsoft Edge WebView2 Runtime was not detected.')
    return webview


def diagnostics():
    result = {'repair': '0.2.1 startup repair 2', 'python': sys.version,
              'windows': platform.platform(), 'executable': sys.executable, 'packages': {}}
    for name in ('pywebview', 'pythonnet', 'clr_loader', 'cffi'):
        try:
            result['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['packages'][name] = 'missing'
    if sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
                result['net_framework_release'] = winreg.QueryValueEx(key, 'Release')[0]
        except OSError as error:
            result['net_framework_release'] = str(error)
    result['desktop_files'] = {}
    site = Path(sys.base_prefix) / 'Lib/site-packages'
    for name in ('pythonnet/runtime/Python.Runtime.dll', 'clr_loader/ffi/dlls/amd64/ClrLoader.dll'):
        p = site / name
        record = {'exists': p.is_file()}
        if p.is_file():
            record['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
            if sys.platform == 'win32':
                try:
                    zone = Path(str(p) + ':Zone.Identifier').read_text(errors='replace')
                    record['zone_id'] = next((s for s in zone.splitlines() if s.startswith('ZoneId=')), 'present')
                except OSError:
                    record['zone_id'] = 'absent or unreadable'
        result['desktop_files'][name] = record
    return result


def log_error(stage):
    root = data_folder()
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'desktop_error.txt'
    failure = traceback.format_exc()
    try:
        detail = json.dumps(diagnostics(), indent=2)
    except Exception:
        detail = 'Additional diagnostics unavailable.'
    path.write_text(f'Stage: {stage}\n\n{failure}\n\n{detail}\n', encoding='utf8')
    return path


def launch_browser_mode():
    executable = Path(sys.executable)
    if sys.platform == 'win32':
        executable = executable.with_name('python.exe')
    return subprocess.Popen([str(executable), str(APP / 'browser_mode.py')],
                            cwd=str(APP), creationflags=0x00000010 if sys.platform == 'win32' else 0)


def show_failure(stage, error, allow_browser=False):
    path = log_error(stage)
    if sys.platform != 'win32':
        print(f'{stage}: {error}\nDetails: {path}', file=sys.stderr)
        return
    text = f'Tunnel Studio could not open its {stage}.\n\n{str(error)[:600]}\n\nDetails: {path}'
    if allow_browser:
        text += '\n\nOpen browser recovery mode? It uses the same survey workspace on this laptop.'
        answer = ctypes.windll.user32.MessageBoxW(None, text, 'Tunnel Studio startup', 0x24)
        if answer == 6:
            try:
                launch_browser_mode()
            except Exception as recovery_error:
                show_failure('browser recovery', recovery_error)
    else:
        ctypes.windll.user32.MessageBoxW(None, text, 'Tunnel Studio startup', 0x10)


if __name__ == '__main__':
    print(json.dumps(diagnostics(), indent=2), flush=True)
    try:
        desktop_backend()
        print('PASS: Python/.NET bridge, Windows Forms and WebView2 imports completed.', flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
