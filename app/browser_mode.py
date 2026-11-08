# Copyright (c) 2026 Damian Gulczynski.
"""Recovery UI using the existing browser, without Python.NET or WebView2."""
import sys
import threading
import time
import traceback
import webbrowser
from startup_support import log_error


def main():
    import server
    from release import VERSION
    server.acquire_project_lock()
    http = None
    try:
        server.restore()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        url = f'http://127.0.0.1:{http.server_port}'
        print(f'Tunnel Studio {VERSION} - browser recovery\n\nOpen: {url}\n'
              'Keep this console open while using Tunnel Studio.\n'
              'To stop safely, return here and press Ctrl+C.\n'
              'Your survey files stay on this laptop.\n', flush=True)
        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                print('Open the address above in your browser.', flush=True)
        threading.Thread(target=open_browser, daemon=True).start()
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            print('\nStopping. Waiting for any active processing to finish...', flush=True)
        finally:
            while server.JOB['busy']:
                time.sleep(.1)
    finally:
        if http is not None:
            http.server_close()
        server._PROJECT_LOCK.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        path = log_error('browser recovery')
        traceback.print_exc()
        print(f'\nDetails saved to {path}', flush=True)
        try:
            input('Press Enter to close...')
        except EOFError:
            pass
        sys.exit(1)
