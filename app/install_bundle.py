# Copyright (c) 2026 Damian Gulczynski.
"""Per-user Windows installation of the fully bundled release. No pip/build required."""
from pathlib import Path
from datetime import datetime
import ctypes,json,os,shutil,subprocess,sys,traceback,uuid
from release import VERSION,COPYRIGHT

def message(text,flags=0):
    return ctypes.windll.user32.MessageBoxW(None,text,'Tunnel Studio '+VERSION,flags)

def main():
    if sys.platform!='win32':raise RuntimeError('This installer runs on Windows only.')
    source=Path(__file__).resolve().parent.parent
    for name in ['TunnelStudio.exe','runtime/pythonw.exe','app/desktop.py']:
        if not (source/name).is_file():raise RuntimeError('Extract the complete ZIP before running SetupTunnelStudio.exe.')
    if message('Install Tunnel Studio '+VERSION+' for your Windows account?\n\nA desktop and Start menu shortcut will be created. Existing survey data will be kept. Close any running Tunnel Studio window first.\n\n'+COPYRIGHT,1)!=1:return
    data=Path(os.environ['LOCALAPPDATA'])/'TunnelStudio';data.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=open(data/'session.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise RuntimeError('Close the running Tunnel Studio application and try again.')
    base=Path(os.environ['LOCALAPPDATA'])/'Programs'/'TunnelStudio'/'releases';base.mkdir(parents=True,exist_ok=True)
    target=base/VERSION
    if source.resolve()!=target.resolve():
        stage=base/('installing_'+uuid.uuid4().hex)
        try:
            shutil.copytree(source,stage,ignore=shutil.ignore_patterns('__pycache__','*.log'))
            if target.exists():target.rename(base/(VERSION+'_previous_'+datetime.now().strftime('%Y%m%d_%H%M%S')))
            stage.rename(target)
        finally:
            if stage.exists():shutil.rmtree(stage)
    if (data/'project.json').exists():
        backups=data/'project_indexes';backups.mkdir(exist_ok=True)
        shutil.copy2(data/'project.json',backups/('before_'+VERSION+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json'))
    env=os.environ.copy();env['TUNNEL_EXE']=str(target/'TunnelStudio.exe');env['TUNNEL_DIR']=str(target)
    script='''$ErrorActionPreference='Stop'
$shell=New-Object -ComObject WScript.Shell
$locations=@([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))
foreach($folder in $locations){
 $link=$shell.CreateShortcut((Join-Path $folder 'Tunnel Studio.lnk'))
 $link.TargetPath=$env:TUNNEL_EXE
 $link.WorkingDirectory=$env:TUNNEL_DIR
 $link.Description='Tunnel Studio - Damian Gulczynski'
 $link.Save()
}
'''
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,check=True,creationflags=0x08000000,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (base/'current_release.json').write_text(json.dumps({'version':VERSION,'program_folder':str(target)}),encoding='utf8')
    lock.close()
    if message('Tunnel Studio '+VERSION+' is installed.\n\nUse the Tunnel Studio shortcut on your desktop or Start menu.\n\nOpen it now?',4)==6:
        subprocess.Popen([str(target/'TunnelStudio.exe')],cwd=target)

if __name__=='__main__':
    try:main()
    except Exception as error:
        path=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'TunnelStudio'/'setup_error.txt';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(traceback.format_exc(),encoding='utf8')
        message('Setup did not complete.\n\n'+str(error)+'\n\nDetails saved to:\n'+str(path),16)
