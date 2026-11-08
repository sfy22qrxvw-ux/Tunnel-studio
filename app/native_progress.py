# Copyright (c) 2026 Damian Gulczynski.
"""Native Windows progress window for long Tunnel Studio operations.

It is deliberately independent of WebView2 so the user can still see activity if
an embedded renderer is busy.  Where progress text contains n/total values, the
bar shows determinate progress in the selected workspace accent.
"""
from pathlib import Path
import json,re,sys,time

PAIR=re.compile(r'([0-9][0-9,]*)\s*/\s*([0-9][0-9,]*)')
STAGE=re.compile(r'\b([1-9][0-9]*)/([1-9][0-9]*)\b')

def _appearance(status_path, theme=None, colourway=None):
    from appearance_palettes import DEFAULT, resolved_theme
    from ui_preferences import AppearancePreferences
    saved=AppearancePreferences(Path(status_path).parent/'ui-preferences.json').get()
    return resolved_theme(theme or saved.get('theme')), colourway or saved.get('palette',DEFAULT)


def palette(status_path, theme=None, colourway=None):
    from appearance_palettes import colours
    return colours(*_appearance(status_path,theme,colourway))


def percentage(message):
    msg=str(message or '')
    pairs=[]
    for a,b in PAIR.findall(msg):
        try:
            a=int(a.replace(',',''));b=int(b.replace(',',''))
            if b>0 and 0<=a<=b:pairs.append((a,b))
        except Exception:pass
    stage=None
    m=STAGE.search(msg)
    if m:
        a,b=map(int,m.groups())
        if b>0 and a<=b:stage=(a,b)
    # Prefer the final n/total pair when it is not simply the stage fraction.
    work=pairs[-1] if pairs else None
    if stage and work and work!=stage:
        return max(0,min(99,int(((stage[0]-1)+work[0]/work[1])/stage[1]*100)))
    if work and work[1]>=20:return max(0,min(99,int(work[0]/work[1]*100)))
    if stage:return max(0,min(99,int((stage[0]-1)/stage[1]*100)))
    return None

TITLES = {
    'surface':'Extracting tunnel surface',
    'mesh':'Creating tunnel mesh',
    'registration':'Importing registration scan',
    'job':'Updating survey workspace',
    'analysis':'Analysing survey data',
    'shape matching':'Finding similar shapes',
    'pocket inspection':'Inspecting shape match',
    'local-circle cleaning':'Reviewing lining offsets',
    'measurement':'Measuring survey geometry',
    'inspection':'Comparing survey surfaces',
    'inspection-export':'Exporting inspection results',
    'confirmed shape library':'Saving shape examples',
    'local feature selection':'Finding lining features',
    'project-folder-copy':'Copying project data',
}


def duration(seconds):
    total=max(0,int(seconds))
    hours,remainder=divmod(total,3600)
    minutes,seconds=divmod(remainder,60)
    return (f'{hours:d}:' if hours else '')+f'{minutes:02d}:{seconds:02d}'


def progress_summary(data, elapsed):
    message=str(data.get('message') or 'Working…')
    activity,_,detail=message.partition(' · ')
    if data.get('busy'):
        percent=percentage(message)
        remaining=duration(elapsed*(100-percent)/percent) if percent is not None and percent>1 else 'Estimating…'
        return 'IN PROGRESS',TITLES.get(data.get('kind'),'Processing survey data'),activity,detail,percent,remaining
    if data.get('error'):
        return 'STOPPED','Operation stopped',str(data['error']),'',0,'—'
    if data.get('cancelled'):
        return 'CANCELLED','Operation cancelled',activity,detail,0,'—'
    return 'COMPLETE','Ready to continue',activity,detail,100,'00:00'


def run(status_path,cancel_path,theme=None,colourway=None):
    import clr
    clr.AddReference('System.Drawing')
    clr.AddReference('System.Windows.Forms')
    from System.Drawing import ColorTranslator,ContentAlignment,Font,FontStyle,Icon,Point,Size,SizeF,SolidBrush
    from System.Drawing.Drawing2D import SmoothingMode
    from System.Windows.Forms import AccessibleRole,Application,AutoScaleMode,Button,FlatStyle,Form,FormBorderStyle,FormStartPosition,Label,Panel,Timer
    from window_chrome import apply_native_caption
    Application.EnableVisualStyles()
    Application.SetCompatibleTextRenderingDefault(False)
    status_path,cancel_path=Path(status_path),Path(cancel_path)
    theme,colourway=_appearance(status_path,theme,colourway)
    values=palette(status_path,theme,colourway)
    colours={key:ColorTranslator.FromHtml(value) for key,value in values.items()}

    form=Form()
    form.Text='Tunnel Studio · Processing'
    form.AutoScaleDimensions=SizeF(96.,96.)
    form.AutoScaleMode=AutoScaleMode.Dpi
    form.ClientSize=Size(566,228)
    form.FormBorderStyle=FormBorderStyle.FixedDialog
    form.MaximizeBox=False
    form.StartPosition=FormStartPosition.CenterScreen
    form.BackColor=colours['background']
    form.TopMost=True
    icon=Icon(str(Path(__file__).with_name('web')/'tunnel.ico'))
    form.Icon=icon
    def style_caption(*_):
        apply_native_caption(int(form.Handle.ToInt64()),theme,values)
    form.HandleCreated+=style_caption
    if form.IsHandleCreated:
        style_caption()

    fonts=[]
    def label(text,x,y,width,height,size,tone='text',bold=False,right=False):
        control=Label()
        control.Text=text
        control.UseMnemonic=False
        control.AutoEllipsis=True
        control.Location=Point(x,y)
        control.Size=Size(width,height)
        control.Font=Font('Segoe UI',size,FontStyle.Bold if bold else FontStyle.Regular)
        fonts.append(control.Font)
        control.ForeColor=colours[tone]
        control.TextAlign=ContentAlignment.MiddleRight if right else ContentAlignment.MiddleLeft
        form.Controls.Add(control)
        return control

    phase=label('IN PROGRESS',24,14,270,18,8.5,'accent',True)
    title=label('Processing survey data',24,33,424,39,17.,bold=True)
    completion=label('—',455,20,86,46,24.,'accent',True,True)
    message=label('Starting…',24,83,518,25,11.)
    detail=label('',24,108,518,20,9.5,'muted')
    bar=Panel()
    bar.Location=Point(24,146)
    bar.Size=Size(518,7)
    bar.AccessibleRole=AccessibleRole.ProgressBar
    bar.AccessibleName='Operation progress'
    form.Controls.Add(bar)

    divider=Panel()
    divider.Location=Point(24,170)
    divider.Size=Size(518,1)
    divider.BackColor=colours['border']
    form.Controls.Add(divider)
    label('ELAPSED',24,181,115,17,8.,'muted',True)
    elapsed_value=label('00:00',24,197,122,21,10.5,bold=True)
    label('EST. REMAINING',160,181,140,17,8.,'muted',True)
    remaining_value=label('Estimating…',160,197,144,21,10.5,bold=True)
    cancel=Button()
    cancel.Text='Cancel'
    cancel.Location=Point(444,183)
    cancel.Size=Size(98,34)
    cancel.BackColor=colours['surface']
    cancel.ForeColor=colours['text']
    cancel.FlatStyle=FlatStyle.Flat
    cancel.FlatAppearance.BorderColor=colours['border']
    cancel.FlatAppearance.MouseOverBackColor=colours['hover']
    form.Controls.Add(cancel)
    form.CancelButton=cancel

    state={'done_since':None,'started':time.monotonic(),'percent':None}
    track_brush=SolidBrush(colours['surface'])
    accent_brush=SolidBrush(colours['accent'])

    def paint(sender,event):
        g=event.Graphics
        g.SmoothingMode=SmoothingMode.AntiAlias
        w,h=bar.Width,bar.Height

        def rounded(brush,x,width):
            if width<=0:return
            cap=min(h,width)
            g.FillEllipse(brush,x,0,cap,h)
            if width>cap:
                g.FillRectangle(brush,x+cap//2,0,width-cap,h)
                g.FillEllipse(brush,x+width-cap,0,cap,h)

        rounded(track_brush,0,w)
        pct=state['percent']
        if pct is not None:
            rounded(accent_brush,0,round(w*pct/100))
        else:
            span=max(1,w//5)
            offset=round((w-span)*((time.monotonic()-state['started'])%1.8)/1.8)
            rounded(accent_brush,offset,span)

    bar.Paint+=paint
    def request_cancel(*_):
        try:
            cancel_path.write_text('cancel',encoding='utf8')
            cancel.Enabled=False
        except OSError:
            pass
    cancel.Click+=request_cancel

    timer=Timer()
    timer.Interval=160
    def tick(*_):
        try:
            data=json.loads(status_path.read_text(encoding='utf8'))
        except (OSError,ValueError):
            return
        now=time.monotonic()
        elapsed=max(0,now-state['started'])
        state_label,heading,activity,metrics,pct,eta=progress_summary(data,elapsed)
        if data.get('busy') and not cancel.Enabled:
            state_label,activity,metrics='STOPPING','Waiting for a safe checkpoint…',''
        phase.Text,title.Text,message.Text,detail.Text=state_label,heading,activity,metrics
        state['percent']=pct
        completion.Text=f'{pct}%' if pct is not None else '—'
        elapsed_value.Text,remaining_value.Text=duration(elapsed),eta
        bar.AccessibleDescription=completion.Text if pct is not None else 'Estimating progress'
        bar.Invalidate()
        if data.get('busy'):
            state['done_since']=None
        else:
            cancel.Enabled=False
            if state['done_since'] is None:
                state['done_since']=now
            elif now-state['done_since']>2.4:
                timer.Stop()
                form.Close()

    timer.Tick+=tick
    form.FormClosing+=lambda *_:request_cancel() if cancel.Enabled else None
    timer.Start()
    try:
        Application.Run(form)
    finally:
        timer.Dispose()
        form.Dispose()
        icon.Dispose()
        track_brush.Dispose()
        accent_brush.Dispose()
        for font in fonts:
            font.Dispose()


if __name__=='__main__':
    try:
        run(*sys.argv[1:5])
    except Exception as error:
        import runtime_diagnostics
        runtime_diagnostics.install('native progress')
        runtime_diagnostics.exception('Native progress window failed',error)
