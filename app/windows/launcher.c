/* Copyright (c) 2026 Damian Gulczynski. */
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#ifndef INSTALLER
#define INSTALLER 0
#endif
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,PWSTR args,int show) {
    wchar_t folder[32768],python[32768],script[32768],command[32768];
    DWORD size=GetModuleFileNameW(NULL,folder,32768);
    if (!size || size>=32768) return 1;
    wchar_t *last=wcsrchr(folder,L'\\');
    if (!last) return 1;
    *last=0;
    if (swprintf(python,32768,L"%ls\\runtime\\pythonw.exe",folder)<0 ||
        swprintf(script,32768,L"%ls\\app\\%ls",folder,INSTALLER?L"install_bundle.py":L"desktop.py")<0) return 1;
    if (GetFileAttributesW(python)==INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script)==INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,L"Please extract the entire ZIP first. Keep the app and runtime folders beside this EXE.",L"Tunnel Studio",MB_OK|MB_ICONERROR);return 2;
    }
    if (swprintf(command,32768,L"\"%ls\" \"%ls\"",python,script)<0) return 1;
    STARTUPINFOW startup={0};startup.cb=sizeof(startup);
    PROCESS_INFORMATION process={0};
    if (!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,folder,&startup,&process)) {
        wchar_t error[256];swprintf(error,256,L"Windows could not start Tunnel Studio (error %lu). Please send this message with version 0.17.0.",GetLastError());
        MessageBoxW(NULL,error,L"Tunnel Studio",MB_OK|MB_ICONERROR);return 3;
    }
    CloseHandle(process.hThread);CloseHandle(process.hProcess);return 0;
}
