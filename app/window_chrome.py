# Copyright (c) 2026 Damian Gulczynski.
"""Branded native window captions, with the original window and close guards.

The caption is a WinForms control outside WebView2, so HTML modal dialogs do
not disable its controls. Windows retains its sizing frame and system menu.
No WndProc subclass, native callback pointer or runtime patch is installed.
If construction fails, the original Windows caption remains available.
"""
import ctypes
import os
import sys
from appearance_palettes import DEFAULT, colours, resolved_theme


def caption_style(style):
    """Remove only the caption; retain resize, taskbar and system-menu styles."""
    return (int(style)&0xffffffff)&~0x00c00000


def palette(theme, colourway=DEFAULT):
    c=colours(theme,colourway)
    return dict(background=c['caption'],foreground=c['text'],muted=c['muted'],hover=c['hover'],accent=c['accent'])


def caption_attributes(theme, values):
    def colorref(key):
        return int.from_bytes(bytes.fromhex(values[key][1:]), 'little')
    return {20:int(theme=='dark'),34:colorref('border'),35:colorref('caption'),36:colorref('text')}


def apply_native_caption(hwnd, theme, values):
    """Set a standard Windows caption's colours while keeping its controls."""
    if sys.platform!='win32':
        return
    try:
        setter=ctypes.WinDLL('dwmapi').DwmSetWindowAttribute
        setter.argtypes=(ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint)
        setter.restype=ctypes.c_long
        for attribute, value in caption_attributes(theme,values).items():
            data=ctypes.c_uint32(value)
            status=setter(hwnd,attribute,ctypes.byref(data),ctypes.sizeof(data))
            if attribute==20 and status:
                setter(hwnd,19,ctypes.byref(data),ctypes.sizeof(data))
    except (AttributeError,OSError):
        pass


def perform(window,action):
    """Use pywebview's normal window operations, including guarded destruction."""
    if action=='close':return window.destroy()
    if action=='minimise':return window.minimize()
    if action=='maximise':return window.maximize()
    if action=='restore':return window.restore()
    raise ValueError('Unknown window action.')


class WindowChrome:
    def __init__(self,window,caption='Survey workspace',theme='light',colourway=DEFAULT):
        self.window=window;self.caption=str(caption)[:120];self.theme=resolved_theme(theme);self.colourway=colourway
        self.panel=None;self.form=None;self.content=None;self.fonts=[];self.handlers=[]
        self._old_style=None;self._old_dock=None;self._user32=None

    def attach(self):
        if sys.platform!='win32' or os.environ.get('TUNNEL_STUDIO_NATIVE_FRAME')=='1':return self
        self.window._tunnel_chrome=self
        self.window.events.shown+=self._shown
        return self

    def _shown(self,*_):
        try:
            from System import Action
            self.window.native.Invoke(Action(self._build))
        except Exception as error:
            self._report(error)
        finally:
            # This callback runs outside the native UI Invoke. Waiting for the
            # first document while holding that UI thread would block WebView2.
            try:self.window.run_js('window.TunnelWindowChrome?.refresh()')
            except Exception:pass

    @staticmethod
    def _report(error):
        import runtime_diagnostics
        runtime_diagnostics.exception('Custom window caption unavailable; standard caption retained',error)

    def _build(self):
        from System.Drawing import Color,Font,FontStyle,Point,Size
        from System.Windows.Forms import Panel,Label,Button,DockStyle,FlatStyle,FormWindowState,Screen,MouseButtons
        from release import VERSION
        self.form=form=self.window.native
        self.content=content=form.browser.webview
        self._old_dock=content.Dock
        user32=self._user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.GetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int];user32.GetWindowLongW.restype=ctypes.c_long
        user32.SetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_long];user32.SetWindowLongW.restype=ctypes.c_long
        user32.SendMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t];user32.SendMessageW.restype=ctypes.c_ssize_t
        user32.SetWindowPos.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint]
        hwnd=int(form.Handle.ToInt64());self._old_style=user32.GetWindowLongW(hwnd,-16)
        try:
            self.panel=panel=Panel();panel.Name='TunnelStudioWindowCaption';panel.TabStop=False
            self.fonts=[Font('Segoe UI',10.,FontStyle.Bold),Font('Segoe UI',9.),Font('Segoe UI Symbol',12.)]
            self.brand=Label();self.brand.Text='◉  TUNNEL STUDIO';self.brand.Font=self.fonts[0]
            self.version=Label();self.version.Text=VERSION;self.version.Font=self.fonts[1]
            self.title=Label();self.title.Text=self.caption;self.title.Font=self.fonts[1]
            self.title.AutoEllipsis=True
            self.buttons=[]
            for text,name in (('−','Minimise'),('□','Maximise'),('×','Close')):
                button=Button();button.Text=text;button.AccessibleName=name;button.Font=self.fonts[2]
                button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderSize=0;button.TabStop=True
                panel.Controls.Add(button);self.buttons.append(button)
            for label in (self.brand,self.version,self.title):panel.Controls.Add(label)
            def toggle(*_):
                if form.WindowState==FormWindowState.Maximized:perform(self.window,'restore')
                else:
                    form.MaximizedBounds=Screen.FromControl(form).WorkingArea
                    perform(self.window,'maximise')
            def drag(sender,event):
                if event.Button!=MouseButtons.Left:return
                if event.Clicks==2:toggle();return
                if form.WindowState==FormWindowState.Maximized:return
                user32.ReleaseCapture();user32.SendMessageW(hwnd,0x00a1,2,0)
            def minimise(*_):perform(self.window,'minimise')
            def close(*_):perform(self.window,'close')
            self.handlers=[drag,toggle,minimise,close,self._layout,self._closed]
            for control in (panel,self.brand,self.version,self.title):control.MouseDown+=drag
            self.buttons[0].Click+=minimise;self.buttons[1].Click+=toggle;self.buttons[2].Click+=close
            form.Controls.Add(panel);content.Dock=getattr(DockStyle,'None')
            form.Resize+=self._layout;form.DpiChanged+=self._layout;form.FormClosed+=self._closed
            self._apply_theme();self._layout()
            # Apply only after the replacement controls and layout exist.
            ctypes.set_last_error(0)
            previous=user32.SetWindowLongW(hwnd,-16,ctypes.c_long(caption_style(self._old_style)).value)
            if previous==0 and ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
            user32.SetWindowPos(hwnd,None,0,0,0,0,0x0037)
            panel.BringToFront()
        except Exception:
            self._rollback()
            raise

    def _layout(self,*_):
        if self.panel is None or self.form.IsDisposed:return
        from System.Drawing import Point,Size
        from System.Windows.Forms import FormWindowState
        form=self.form;scale=max(1.,float(form.DeviceDpi)/96.);px=lambda v:max(1,int(round(v*scale)))
        width=max(1,int(form.ClientSize.Width));height=px(42);button=px(46)
        self.panel.Location=Point(0,0);self.panel.Size=Size(width,height)
        narrow=width<px(650)
        self.brand.Text='◉  TUNNEL' if width<px(500) else '◉  TUNNEL STUDIO'
        self.brand.Location=Point(px(15),px(12));self.brand.Size=Size(px(110 if width<px(500) else 163),px(22))
        self.version.Location=Point(px(180),px(13));self.version.Size=Size(px(58),px(20))
        self.version.Visible=not narrow
        title_x=130 if width<px(500) else 190 if narrow else 250
        self.title.Location=Point(px(title_x),px(13));self.title.Size=Size(max(1,width-px(title_x+20)-button*3),px(20))
        for i,control in enumerate(self.buttons):control.Location=Point(width-button*(3-i),0);control.Size=Size(button,height)
        self.buttons[1].Text='▣' if form.WindowState==FormWindowState.Maximized else '□'
        self.buttons[1].AccessibleName='Restore' if form.WindowState==FormWindowState.Maximized else 'Maximise'
        self.content.Location=Point(0,height);self.content.Size=Size(width,max(1,int(form.ClientSize.Height)-height))

    def _apply_theme(self):
        from System.Drawing import Color
        colours=palette(self.theme,self.colourway)
        def convert(value):return Color.FromArgb(int(value[1:3],16),int(value[3:5],16),int(value[5:7],16))
        bg=convert(colours['background']);fg=convert(colours['foreground'])
        self.panel.BackColor=bg
        for control in (self.brand,self.version,self.title,*self.buttons):control.BackColor=bg;control.ForeColor=fg
        self.brand.ForeColor=convert(colours['accent']);self.version.ForeColor=convert(colours['muted'])
        for control in self.buttons:control.FlatAppearance.MouseOverBackColor=convert(colours['hover'])
        self.buttons[2].FlatAppearance.MouseOverBackColor=Color.FromArgb(185,42,60)
        self.buttons[2].FlatAppearance.MouseDownBackColor=Color.FromArgb(150,30,46)
        # White close text remains readable on its destructive hover colour.
        def enter(*_):self.buttons[2].ForeColor=Color.White
        def leave(*_):self.buttons[2].ForeColor=fg
        if getattr(self,'_hover_handlers',None):
            self.buttons[2].MouseEnter-=self._hover_handlers[0];self.buttons[2].MouseLeave-=self._hover_handlers[1]
        self._hover_handlers=(enter,leave);self.buttons[2].MouseEnter+=enter;self.buttons[2].MouseLeave+=leave

    def set_theme(self,theme,colourway=DEFAULT):
        self.theme=resolved_theme(theme);self.colourway=colourway
        if self.panel is not None and not self.form.IsDisposed:
            from System import Action
            self.form.BeginInvoke(Action(self._apply_theme))

    def _rollback(self):
        try:
            if self._old_style is not None:
                handle=int(self.form.Handle.ToInt64());self._user32.SetWindowLongW(handle,-16,self._old_style)
                self._user32.SetWindowPos(handle,None,0,0,0,0,0x0037)
            if self.content is not None:self.content.Dock=self._old_dock
            if self.panel is not None:self.form.Controls.Remove(self.panel);self.panel.Dispose()
            if self.form is not None:
                for event in ('Resize','DpiChanged'):
                    try:getattr(self.form,event).__isub__(self._layout)
                    except Exception:pass
            self.panel=None
            for font in self.fonts:font.Dispose()
            self.fonts=[]
        except Exception as error:self._report(error)

    def _closed(self,*_):
        for font in self.fonts:
            try:font.Dispose()
            except Exception:pass
        self.fonts=[];self.handlers=[]
