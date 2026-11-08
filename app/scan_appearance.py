# Copyright (c) 2026 Damian Gulczynski.
"""Persist independent scan display styles without changing measured attributes.

A scan can follow the global viewer mode, use its own measured RGB/intensity/
elevation mode, or use a chosen uniform colour. Overrides affect disposable
display samples only. Geometry/export identities and source buffers are unchanged;
the ordinary workspace history restores style edits atomically.
"""
import copy
import re

MODES={'global','solid','rgb','intensity-gray','intensity-hue','elevation','bundle'}
HEX=re.compile(r'#[0-9a-fA-F]{6}')


def resolve(item,fallback,mode):
    if item.get('kind')!='scan':return fallback,mode
    chosen=item.get('display_mode','global')
    if not isinstance(chosen,str):return fallback,mode
    if chosen=='solid':
        value=item.get('display_colour')
        if isinstance(value,str) and HEX.fullmatch(value):return [int(value[i:i+2],16)/255 for i in (1,3,5)],'bundle'
    elif chosen in MODES-{'global','solid'}:return fallback,chosen
    return fallback,mode


def apply(s,body):
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation before changing scan colours.')
    item=next((x for x in s.STATE.get('job_items',[]) if x.get('id')==body.get('id') and x.get('kind')=='scan'),None)
    if item is None:raise ValueError('Choose an existing point-cloud scan.')
    mode=body.get('mode');colour=body.get('colour')
    if not isinstance(mode,str) or mode not in MODES:raise ValueError('Choose a supported scan display mode.')
    if mode=='solid' and (not isinstance(colour,str) or not HEX.fullmatch(colour)):raise ValueError('Choose a colour in #RRGGBB format.')
    previous=copy.deepcopy(s.STATE)
    try:
        if mode=='global':item.pop('display_mode',None);item.pop('display_colour',None)
        else:
            item['display_mode']=mode
            if mode=='solid':item['display_colour']=colour.lower()
            else:item.pop('display_colour',None)
        s.save()
    except BaseException:s.STATE.clear();s.STATE.update(previous);raise
    return dict(saved=True,id=item['id'],display_mode=item.get('display_mode','global'),display_colour=item.get('display_colour'))


def post(s,h,path,body):
    if path!='/api/scan-appearance':return False
    if body.get('action')!='set':raise ValueError('Choose a scan display style to apply.')
    with s.LOCK:result=apply(s,body)
    h.send(result);return True
