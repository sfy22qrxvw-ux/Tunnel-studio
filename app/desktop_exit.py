# Copyright (c) 2026 Damian Gulczynski.
"""Let the Windows close button offer the same optional cleanup as Close project."""
import json
import threading
import uuid


class ExitReview:
    """FormClosing cannot synchronously run JavaScript on the WinForms thread."""

    def __init__(self, window, busy, guard, timer=threading.Timer):
        self.window = window
        self.busy = busy
        self.guard = guard
        self.timer = timer
        self.lock = threading.Lock()
        self.ticket = None
        self.approved = False
        self.probe = None

    def _post(self, function, *args):
        threading.Thread(target=function, args=args, name='TunnelStudioExit', daemon=True).start()

    def _inform_busy(self):
        try:
            self.window.run_js("if(typeof notice==='function') notice('Finish the active operation or use Stop before closing Tunnel Studio.',true)")
        except Exception:
            pass

    def on_closing(self, sender, args):
        if args.Cancel:
            return
        # An OS shutdown or task manager request proceeds through the original
        # close guard. No project cleanup is initiated by those requests.
        if str(args.CloseReason) != 'UserClosing':
            self.guard()
            return
        if self.busy():
            args.Cancel = True
            self._post(self._inform_busy)
            return
        with self.lock:
            if self.approved:
                allow = True
            else:
                allow = False
                args.Cancel = True
                if self.probe is not None:
                    return
                self.ticket = self.ticket or uuid.uuid4().hex
                ticket = self.ticket
                probe = self.probe = object()
        if allow:
            self.guard()
            return

        # A crashed renderer can never acknowledge a prompt. Limit the handshake,
        # then close without running cleanup. An acknowledged review can stay
        # open indefinitely. A second X rechecks a renderer that later went away.
        timeout = self.timer(5.0, self._acknowledge, (ticket, probe, False))
        timeout.daemon = True
        timeout.start()

        def dispatch():
            try:
                accepted = self.window.evaluate_js('window.TunnelDesktopClose?.request(' + json.dumps(ticket) + ') === true')
                self._acknowledge(ticket, probe, accepted is True)
            except Exception:
                self._acknowledge(ticket, probe, False)
            finally:
                timeout.cancel()
        self._post(dispatch)

    def _acknowledge(self, ticket, probe, available):
        with self.lock:
            if self.ticket != ticket or self.probe is not probe:
                return
            self.probe = None
            if available:
                return
            self.ticket = None
            self.approved = not self.busy()
            close = self.approved
        if close:
            self._post(self._close)

    def finish(self, ticket, outcome):
        if not isinstance(ticket, str) or outcome not in ('skipped', 'cleaned', 'cancelled'):
            return False
        with self.lock:
            if self.ticket != ticket:
                return False
            self.ticket = self.probe = None
            self.approved = outcome != 'cancelled' and not self.busy()
            close = self.approved
        if close:
            self._post(self._close)
        return True

    def _close(self):
        try:
            self.window.destroy()
        except Exception:
            # If native destruction is unavailable, a further X can proceed
            # through the original FormClosing shutdown guard.
            pass
