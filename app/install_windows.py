# Copyright (c) 2026 Damian Gulczynski.
"""Install a locally built standalone bundle; no administrator rights needed."""
from pathlib import Path
import os, shutil, subprocess, sys
if sys.platform!='win32': raise SystemExit('Build and run this installer on Windows.')
source=Path(__file__).resolve().parent/'dist'/'TunnelStudio'
if not (source/'TunnelStudio.exe').is_file(): raise SystemExit('Build missing. Run BUILD_WINDOWS_APP.bat first.')
target=Path(os.environ['LOCALAPPDATA'])/'Programs'/'TunnelStudio'
# Keep project files separate in LOCALAPPDATA/TunnelStudio.
target.parent.mkdir(parents=True,exist_ok=True)
shutil.copytree(source,target,dirs_exist_ok=True)
env=os.environ.copy(); env['TUNNEL_EXE']=str(target/'TunnelStudio.exe'); env['TUNNEL_DIR']=str(target)
script='''$shell=New-Object -ComObject WScript.Shell
$desktop=[Environment]::GetFolderPath('Desktop')
$link=$shell.CreateShortcut((Join-Path $desktop 'Tunnel Studio.lnk'))
$link.TargetPath=$env:TUNNEL_EXE
$link.WorkingDirectory=$env:TUNNEL_DIR
$link.Description='Tunnel Studio - local tunnel survey workspace'
$link.Save()
'''
subprocess.run(['powershell.exe','-NoProfile','-Command',script],env=env,check=True)
print('Installed:',target)
