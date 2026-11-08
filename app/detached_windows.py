# Copyright (c) 2026 Damian Gulczynski.
"""Native, independently resizable tool windows sharing the main UI's state."""
from pathlib import Path
import json, re, threading


class ToolBridge:
    def __init__(self,manager,ident):
        self._manager=manager;self._ident=ident

    def snapshot(self,width=640,height=700):
        return self._manager.call('snapshot',self._ident,max(300,min(5000,int(width))),max(200,min(4000,int(height))))

    def dispatch(self,event):
        if not isinstance(event,dict):raise ValueError('Invalid tool event.')
        if len(json.dumps(event))>200000:raise ValueError('Tool event is too large.')
        return self._manager.call('dispatch',self._ident,event)

    def redock(self):
        return self._manager.close(self._ident)

    def window_chrome_state(self):
        window=self._manager.windows.get(self._ident)
        chrome=getattr(window,'_tunnel_chrome',None)
        return {'active':bool(chrome and chrome.panel is not None)}

    def set_window_theme(self,value,palette='navy'):
        from appearance_palettes import LABELS
        if value not in ('dark','light'):return {'error':'Choose a resolved window theme.'}
        if not isinstance(palette,str) or palette not in LABELS:return {'error':'Choose an available colour palette.'}
        window=self._manager.windows.get(self._ident)
        chrome=getattr(window,'_tunnel_chrome',None)
        if chrome:chrome.set_theme(value,palette)
        return {'applied':True}


class ToolWindows:
    def __init__(self,main,url,layout_file):
        self.main=main;self.url=url;self.layout_file=Path(layout_file);self.windows={};self.lock=threading.RLock()
        try:self.layouts=json.loads(self.layout_file.read_text(encoding='utf8'))
        except (OSError,ValueError):self.layouts={}
        if not isinstance(self.layouts,dict):self.layouts={}

    def call(self,method,*args):
        if method not in ('snapshot','dispatch','redock'):raise ValueError('Invalid tool method.')
        return self.main.evaluate_js('window.DetachedUI.'+method+'('+','.join(json.dumps(x) for x in args)+')')

    def save_layout(self,ident,**values):
        with self.lock:
            self.layouts.setdefault(ident,{}).update(values)
            try:
                self.layout_file.parent.mkdir(parents=True,exist_ok=True)
                temp=self.layout_file.with_suffix('.tmp');temp.write_text(json.dumps(self.layouts),encoding='utf8');temp.replace(self.layout_file)
            except OSError:pass

    def open(self,ident,title):
        import webview
        if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z][\w-]{0,100}',ident):raise ValueError('Invalid tool window.')
        if ident in self.windows:
            self.windows[ident].show();return {'opened':True}
        saved=self.layouts.get(ident,{})
        width=max(360,min(1800,int(saved.get('width',680))));height=max(280,min(1400,int(saved.get('height',760))))
        position={}
        # Keep negative coordinates for monitors left/above the main monitor,
        # but discard a saved position if that monitor is no longer connected.
        x=saved.get('x');y=saved.get('y')
        if isinstance(x,(int,float)) and isinstance(y,(int,float)):
            for screen in getattr(webview,'screens',[]):
                sx=getattr(screen,'x',0);sy=getattr(screen,'y',0)
                if x+width>sx+80 and x<sx+screen.width-80 and y+40>sy and y<sy+screen.height-40:
                    position=dict(x=int(x),y=int(y));break
        bridge=ToolBridge(self,ident)
        window=webview.create_window('Tunnel Studio · '+str(title)[:120],self.url+'/detached.html',js_api=bridge,width=width,height=height,min_size=(360,280),resizable=True,**position)
        from window_chrome import WindowChrome
        chrome=getattr(self.main,'_tunnel_chrome',None)
        WindowChrome(window,caption=str(title),theme=getattr(chrome,'theme','light'),colourway=getattr(chrome,'colourway','navy')).attach()
        self.windows[ident]=window
        def moved(x,y):self.save_layout(ident,x=int(x),y=int(y))
        def resized(width,height):self.save_layout(ident,width=int(width),height=int(height))
        def closed():
            self.windows.pop(ident,None)
            try:self.call('redock',ident)
            except Exception:pass
        window.events.moved+=moved;window.events.resized+=resized;window.events.closed+=closed
        return {'opened':True}

    def set_theme(self,value,palette='navy'):
        for window in list(self.windows.values()):
            chrome=getattr(window,'_tunnel_chrome',None)
            if chrome:chrome.set_theme(value,palette)

    def close(self,ident):
        window=self.windows.get(ident)
        if window:window.destroy()
        return {'closed':True}

    def reset(self):
        for ident in list(self.windows):self.close(ident)
        with self.lock:
            self.layouts={}
            try:self.layout_file.unlink(missing_ok=True)
            except OSError:pass
        return {'reset':True}
