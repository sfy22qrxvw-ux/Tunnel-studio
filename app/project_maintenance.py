# Copyright (c) 2026 Damian Gulczynski.
"""User-invoked backup and migration, using only Python's standard library."""
from pathlib import Path
from datetime import datetime
import json,os,shutil,sys,uuid

def main():
    import tkinter as tk
    from tkinter import filedialog,messagebox
    ui=tk.Tk();ui.withdraw()
    work=Path(os.environ.get('TUNNEL_STUDIO_DATA',str(Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'TunnelStudio')))
    work.mkdir(parents=True,exist_ok=True)
    lock=open(work/'session.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if sys.platform=='win32':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        messagebox.showerror('Close Tunnel Studio','Close the application and its server window, then try again.');return
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    if len(sys.argv)>1 and sys.argv[1]=='backup':
        folder=filedialog.askdirectory(title='Choose where to save a dated project backup')
        if not folder:return
        parent=Path(folder).resolve()
        if parent==work.resolve() or work.resolve() in parent.parents:raise ValueError('Choose a backup location outside the working data folder.')
        destination=parent/('TunnelStudio_Backup_'+stamp)
        shutil.copytree(work,destination,ignore=shutil.ignore_patterns('session.lock','*.tmp'))
        messagebox.showinfo('Backup complete',str(destination));return
    folder=filedialog.askdirectory(title='Select your OLD Tunnel_Studio program folder (containing .work)')
    if not folder:return
    source=Path(folder)/'.work'; path=source/'project.json'
    if not path.is_file():raise ValueError('No .work/project.json found in that folder.')
    if not messagebox.askokcancel('Import old project','This replaces the current project selection. Existing data files stay on disk and the current project index will be backed up. Continue?'):return
    data=json.loads(path.read_text(encoding='utf8')); destination=work/('imported_'+uuid.uuid4().hex)
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('session.lock','*.tmp'))
    for key in ['file','mask_file']:
        if (data.get('cloud') or {}).get(key):
            old=Path(data['cloud'][key]);candidate=destination/old.name
            if not candidate.is_file():raise ValueError('A required cache file is missing: '+old.name)
            data['cloud'][key]=str(candidate)
    if data.get('derived'):
        candidate=destination/Path(data['derived']).name
        if not candidate.is_file():raise ValueError('Derived coordinate cache is missing.')
        data['derived']=str(candidate)
    # This migration is specifically for v0.1, before masks and structure files existed.
    if data.get('edits') or data.get('structures'):raise ValueError('This importer is for v0.1 projects. Use a full backup to move a v0.2 project.')
    current=work/'project.json'
    if current.exists():shutil.copy2(current,work/('project_before_import_'+stamp+'.json'))
    tmp=work/'import_project.tmp';tmp.write_text(json.dumps(data),encoding='utf8');tmp.replace(current)
    messagebox.showinfo('Project imported','Your original files were left untouched. Start Tunnel Studio 0.2 to open the imported project.')

if __name__=='__main__':
    try:main()
    except Exception as e:
        import tkinter.messagebox
        tkinter.messagebox.showerror('Project maintenance',str(e))
        raise
