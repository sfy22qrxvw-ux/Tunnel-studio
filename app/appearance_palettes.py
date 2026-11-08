"""Shared colour choices for the workspace and its native progress windows."""
from html import escape
import json


DEFAULT = 'navy'
LABELS = {
    'navy': 'Navy & teal',
    'graphite': 'Graphite & red',
    'midnight': 'Midnight & blue',
    'slate': 'Slate & violet',
}

_BASE = {
    'dark': dict(bg='#0E1724', panel='#172333', surface='#1E2D40', inset='#111C2C',
                 control='#101D2E', border='#3D5067', text='#EDF3FC', muted='#B1C2D8',
                 accent='#7DE1D0', active='#153F40', active_text='#CBFBEF', hover='#293B52',
                 caption='#102B36', primary='#16786F', primary_hover='#1B8C81'),
    'light': dict(bg='#E9EFF3', panel='#FFFFFF', surface='#F3F7F9', inset='#EAF0F3',
                  control='#FFFFFF', border='#C6D6DE', text='#203B4A', muted='#526A7D',
                  accent='#076F69', active='#E0F3EE', active_text='#065E56', hover='#EDF6F3',
                  caption='#EAF1F6', primary='#086C63', primary_hover='#075A53'),
}

_DARK = {
    'graphite': dict(bg='#100F12', panel='#19171B', surface='#232026', inset='#151318',
                     control='#1A171D', border='#4B4149', caption='#111014', hover='#342B34',
                     accent='#FF887E', active='#402526', active_text='#FFDAD4',
                     primary='#9A3436', primary_hover='#AB383A'),
    'midnight': dict(bg='#0A1220', panel='#121D2F', surface='#1B2A42', inset='#0C1728',
                     control='#101C30', border='#3D5274', caption='#0B1527', hover='#243651',
                     accent='#9DC2FF', active='#223A62', active_text='#DFEBFF',
                     primary='#2857A0', primary_hover='#3164B3'),
    'slate': dict(bg='#141323', panel='#1F1D31', surface='#2B2841', inset='#181728',
                  control='#211F34', border='#514865', caption='#181526', hover='#38314F',
                  accent='#C9B4FF', active='#3C305B', active_text='#F1E7FF',
                  primary='#6845A5', primary_hover='#7853B6'),
}

_LIGHT = {
    'graphite': dict(bg='#F2EFF1', surface='#F8F4F5', inset='#F3ECEE', border='#D8C6C9',
                     caption='#F1E8EB', hover='#F8ECEE', accent='#9D3036', active='#F8E4E6',
                     active_text='#82252B', primary='#963239', primary_hover='#802B31'),
    'midnight': dict(bg='#EBF1F9', surface='#F4F7FC', inset='#EAF0F9', border='#C4D3E9',
                     caption='#E7EEF9', hover='#EEF3FC', accent='#24549C', active='#E1ECFC',
                     active_text='#1F4984', primary='#2B57A0', primary_hover='#244986'),
    'slate': dict(bg='#F0EDF8', surface='#F8F5FC', inset='#F1ECF8', border='#D5CCE6',
                  caption='#EEE8F7', hover='#F2ECFA', accent='#654198', active='#EDE4FA',
                  active_text='#52347B', primary='#67439B', primary_hover='#593A89'),
}


def resolved_theme(value):
    if value in ('light','dark'):
        return value
    if value == 'system':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return 'light' if winreg.QueryValueEx(key,'AppsUseLightTheme')[0] else 'dark'
        except (ImportError,OSError):
            pass
    return 'light'


def colours(theme, palette=DEFAULT):
    theme = resolved_theme(theme)
    choices = _DARK if theme=='dark' else _LIGHT
    values = {**_BASE[theme], **choices.get(palette,{})}
    values['background'] = values['panel'] if theme=='dark' else values['surface']
    if theme=='light' and palette==DEFAULT:
        values['background'] = '#F3F7FB'
    return values


def style_markup():
    """The web UI and WinForms use the same colour values."""
    rules = []
    for theme in ('light','dark'):
        for key in LABELS:
            values = colours(theme,key)
            properties = {f'--ts-theme-{name.replace("_","-")}':value for name,value in values.items()}
            properties.update({'--ts-explorer-accent':values['accent'],'--ts-focus':values['accent'],
                               '--ts-accent-ink':values['accent'],'--accent':values['accent']})
            if theme=='dark':
                properties['--ts-explorer-bg'] = values['panel']
            declarations = ';'.join(f'{name}:{value}' for name,value in properties.items())
            rules.append(f'html[data-theme="{theme}"][data-palette="{key}"]{{{declarations}}}')
    return '<script>window.TUNNEL_PALETTES='+json.dumps(LABELS)+';</script><style>'+'\n'.join(rules)+'</style>'


def controls_markup():
    cards = []
    for key,label in LABELS.items():
        style = ';'.join(f'--swatch-{theme}-{name}:{value}'
                         for theme in ('dark','light') for name,value in colours(theme,key).items()
                         if name in ('caption','panel','surface','border','accent'))
        cards.append(f'<label class="theme-choice"><input type="radio" name="appearance-palette" value="{key}">'
                     f'<span><strong>{escape(label)}</strong></span>'
                     f'<span class="palette-swatch" style="{style}" aria-hidden="true"><i></i></span></label>')
    return '\n'.join(cards)
