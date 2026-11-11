# Copyright (c) 2026 Damian Gulczynski.
"""Desktop shell with explicit bridge configuration and browser recovery."""
import sys
import os
import json
import subprocess
import threading
import time
from pathlib import Path
from release import VERSION
from startup_support import desktop_backend, show_failure
import runtime_diagnostics




def _handoff_to_named_backend():
    """Use a clearly named backend process in Task Manager when available."""
    if sys.platform!='win32' or os.environ.get('TUNNEL_STUDIO_NAMED_BACKEND')=='1':
        return False
    exe=Path(sys.executable)
    if exe.name.lower()!='pythonw.exe':
        return False
    named=exe.with_name('TunnelStudioBackend.exe')
    marker=exe.with_name('TunnelStudioBackend.ok')
    if not (named.is_file() and marker.is_file()):
        return False
    try:
        env=os.environ.copy();env['TUNNEL_STUDIO_NAMED_BACKEND']='1'
        subprocess.Popen([str(named),str(Path(__file__).resolve()),*sys.argv[1:]],cwd=str(Path(__file__).resolve().parent.parent),env=env,creationflags=0x08000000)
        return True
    except Exception:
        return False


class DesktopBridge:
    """Small native bridge used only by the Windows desktop shell."""
    def __init__(self, preferences_path=None, on_appearance=None):
        self.window = None
        self._tool_manager = None
        self._drop_target = None
        self._drop_handler = None
        self._exit_review = None
        self._chrome = None
        self._on_appearance = on_appearance
        from ui_preferences import AppearancePreferences
        self._appearance = AppearancePreferences(preferences_path)

    def get_theme_preference(self):
        return self._appearance.get()

    def set_theme_preference(self, value, palette=None):
        return self._appearance.set(value,palette)

    def window_chrome_state(self):
        return {'active':bool(self._chrome and self._chrome.panel is not None)}

    def set_window_theme(self,value,palette='navy'):
        from appearance_palettes import LABELS
        if value not in ('dark','light'):return {'error':'Choose a resolved window theme.'}
        if not isinstance(palette,str) or palette not in LABELS:return {'error':'Choose an available colour palette.'}
        try:
            if self._on_appearance:self._on_appearance(value,palette)
            if self._chrome:self._chrome.set_theme(value,palette)
            if self._tool_manager:self._tool_manager.set_theme(value,palette)
            return {'applied':True}
        except Exception:return {'applied':False}

    def _native_file_drop(self, event):
        """WebView2 supplies full local paths only through its native DOM event."""
        files = (event.get('dataTransfer') or {}).get('files') or []
        paths = []
        for item in files:
            path = item.get('pywebviewFullPath') if isinstance(item, dict) else None
            if isinstance(path, str) and path and '\x00' not in path and len(path) <= 32768:
                paths.append(path)
        # An incomplete native list must fall back as a whole; never omit files.
        if len(paths) != len(files): paths = []
        ticket = event.get('tunnelDropId')
        self.window.evaluate_js('window.FileDropUI?.nativeDrop(' + json.dumps(paths) + ',' + json.dumps(ticket) + ')')

    def _install_file_drop(self, *_args):
        try:
            from webview.dom import DOMEventHandler
            target = self.window.dom.get_element('body')
            if target is None: return
            handler = DOMEventHandler(self._native_file_drop, prevent_default=True)
            target.events.drop += handler
            self._drop_target, self._drop_handler = target, handler
            self.window.evaluate_js('window.FileDropUI?.nativeReady()')
        except Exception as error:
            runtime_diagnostics.exception('Native file-drop bridge unavailable; browser file transfer remains available', error)

    def open_tool_window(self,ident,title='Tool'):
        if self._tool_manager is None:return {'error':'Tool windows are unavailable in this session.'}
        try:return self._tool_manager.open(ident,title)
        except Exception as error:return {'error':str(error)}

    def close_tool_window(self,ident):
        return self._tool_manager.close(ident) if self._tool_manager else {'closed':True}

    def reset_tool_windows(self):
        return self._tool_manager.reset() if self._tool_manager else {'reset':True}

    def finish_project_exit(self, ticket, outcome):
        return bool(self._exit_review and self._exit_review.finish(ticket, outcome))

    def choose_project_folder(self):
        if self.window is None:return ''
        import webview
        result=self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return str(result[0]) if result and not isinstance(result,str) else (result or '')

    def choose_export_file(self, suggested='Tunnel_Studio_export.e57', fmt='e57'):
        if self.window is None:return {'error':'The desktop save window is unavailable.'}
        try:
            import webview
            from scan_export import FORMATS
            if fmt not in FORMATS and fmt!='dxf':raise ValueError('Unsupported export format.')
            kind=webview.FileDialog.SAVE if hasattr(webview,'FileDialog') else webview.SAVE_DIALOG
            options = {}
            if fmt == 'tstudio':
                from startup_support import data_folder
                pref = data_folder() / 'project_save_preferences.json'
                try:
                    folder = Path(json.loads(pref.read_text(encoding='utf8'))['last_folder'])
                    if folder.is_absolute() and folder.is_dir():
                        options['directory'] = str(folder)
                except (OSError, ValueError, TypeError, KeyError):
                    pass
            result=self.window.create_file_dialog(kind,save_filename=Path(suggested).name,file_types=(f'{fmt.upper()} files (*.{fmt})',),**options)
            return {'path':str(result if isinstance(result,str) else result[0])} if result else {'cancelled':True}
        except Exception as error:return {'error':str(error)}

    def open_project_data_folder(self):
        from startup_support import data_folder, reveal_data_folder
        try:
            return reveal_data_folder(data_folder())
        except (OSError, ValueError) as error:
            return {'error': str(error)}

    def choose_large_file(self, kind='cloud', multiple=False):
        if self.window is None:
            return []
        try:
            import webview
            filters = {
                'shapefile': ('GIS shapefile (*.shp)',),
                'leica_converter': ('Leica REGISTER 360 (register360.exe)', 'Executables (*.exe)'),
                'leica_e57': ('E57 scans (*.e57)',),
                'cloud': ('Point clouds (*.hspca;*.e57;*.las;*.laz;*.ply;*.pts;*.xyz;*.csv;*.txt)', 'All files (*.*)'),
                'registration': ('Point clouds (*.hspca;*.e57;*.las;*.laz;*.ply;*.pts;*.xyz;*.csv;*.txt)', 'All files (*.*)'),
                'surface': ('Point clouds (*.hspca;*.e57;*.las;*.laz;*.ply;*.pts;*.xyz;*.csv;*.txt)', 'All files (*.*)'),
                'project': ('Tunnel Studio projects (*.tstudio)', 'All files (*.*)'),
                'axis': ('Alignment files (*.csv;*.txt;*.xml;*.landxml)', 'All files (*.*)'),
                'rings': ('CSV files (*.csv)', 'All files (*.*)'),
                'job': ('Scans and models (*.hspca;*.e57;*.las;*.laz;*.ply;*.pts;*.xyz;*.csv;*.txt;*.obj;*.zip)', 'All files (*.*)'),
                'job_scan': ('Point clouds (*.hspca;*.e57;*.las;*.laz;*.ply;*.pts;*.xyz;*.csv;*.txt)', 'All files (*.*)'),
                'cad': ('Editable model (*.dxf;*.json)',),
                'job_mesh': ('Models / meshes (*.obj;*.zip)', 'All files (*.*)'),
            }.get(str(kind), ('All files (*.*)',))
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=bool(multiple),
                file_types=filters,
            )
            if not result:
                return []
            if isinstance(result, str):
                return [result]
            return [str(x) for x in result]
        except Exception:
            return []


def main():
    if _handoff_to_named_backend():
        return
    runtime_diagnostics.install('desktop')
    try:
        webview = desktop_backend()
    except Exception as error:
        runtime_diagnostics.exception('Desktop components failed; offering browser recovery', error)
        show_failure('desktop components', error, allow_browser=True)
        return

    server = None
    http = None
    acquired = False
    started = False
    serving = False
    try:
        import server
        server.acquire_project_lock()
        acquired = True
        server.start_empty()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        serving = True
        webview.settings['ALLOW_DOWNLOADS'] = True
        started = True
        bridge = DesktopBridge(server.WORK / 'ui-preferences.json',server.set_progress_appearance)
        window = webview.create_window('Tunnel Studio ' + VERSION + ' — Survey workspace',
                                      f'http://127.0.0.1:{http.server_port}',
                                      width=1440, height=940, min_size=(1000, 700),
                                      js_api=bridge)
        bridge.window = window
        from window_chrome import WindowChrome
        appearance=bridge._appearance.get()
        bridge._chrome=WindowChrome(window,theme=appearance.get('theme'),colourway=appearance.get('palette','navy')).attach()
        window.events.loaded += bridge._install_file_drop
        from detached_windows import ToolWindows
        bridge._tool_manager=ToolWindows(window,f'http://127.0.0.1:{http.server_port}',server.WORK/'tool-window-layout.json')

        # pywebview/WebView2 can raise inside its WinForms FormClosed handler if the
        # browser process has already disappeared (BrowserProcessId == None).  In
        # that state the visible window closes but pythonw.exe can be left alive,
        # keeping the project lock open.  Arm an earlier FormClosing handler that
        # shuts down Tunnel Studio and then guarantees the desktop process exits.
        close_started = threading.Event()
        native_handlers = []

        def close_desktop(*_args):
            if close_started.is_set():
                return
            close_started.set()
            runtime_diagnostics.event('Desktop window closing event received')

            def finish_close():
                try:
                    # Give the UI event a moment to finish, then stop the local
                    # server and release the project lock.  Never wait forever on
                    # an import that has already lost its WebView window.
                    time.sleep(.15)
                    if http is not None:
                        try:
                            http.shutdown()
                        except Exception:
                            pass
                        try:
                            http.server_close()
                        except Exception:
                            pass
                    if acquired:
                        try:
                            server._PROJECT_LOCK.close()
                        except Exception:
                            pass
                finally:
                    # os._exit is intentional here: this path is specifically for
                    # a damaged WebView2/WinForms close sequence where normal
                    # Python/.NET teardown has already proved unreliable.
                    os._exit(0)

            threading.Thread(target=finish_close, name='TunnelStudioCloseGuard', daemon=True).start()

        # pywebview-level close hook is useful on every platform.
        try:
            window.events.closed += close_desktop
        except Exception:
            pass

        if sys.platform == 'win32':
            from desktop_exit import ExitReview
            from storage_cleanup import committing
            bridge._exit_review = ExitReview(window, lambda: bool(server.JOB.get('busy') or committing()), close_desktop)

            def prepare_native_window():
                try:
                    from System import Action
                    from System.Drawing import Icon
                    from System.Windows.Forms import FormClosingEventHandler

                    def apply():
                        try:
                            window.native.Icon = Icon(str(Path(__file__).resolve().parent / 'web/tunnel.ico'))
                        except Exception:
                            pass

                        # FormClosing fires before pywebview's FormClosed handler,
                        # so this guard still runs when BrowserProcessId is missing.
                        try:
                            handler = FormClosingEventHandler(bridge._exit_review.on_closing)
                            native_handlers.append(handler)
                            window.native.FormClosing += handler
                        except Exception:
                            pass

                    window.native.Invoke(Action(apply))
                except Exception:
                    pass

            window.events.shown += prepare_native_window

        webview.start(gui='edgechromium' if sys.platform == 'win32' else None)
    except Exception as error:
        runtime_diagnostics.exception('Desktop window failed; offering recovery', error)
        if http is not None:
            while server.JOB['busy']:
                time.sleep(.1)
            if serving:
                http.shutdown()
            http.server_close()
            http = None
        if acquired:
            server._PROJECT_LOCK.close()
            acquired = False
        show_failure('desktop window' if started else 'survey workspace', error, allow_browser=started)
    finally:
        if http is not None:
            while server.JOB['busy']:
                time.sleep(.1)
            if serving:
                http.shutdown()
            http.server_close()
        if acquired:
            server._PROJECT_LOCK.close()


if __name__ == '__main__':
    main()
