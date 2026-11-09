#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    wchar_t base[32768], python[32768], script[32768], command[65536], error[1024];
    DWORD count = GetModuleFileNameW(NULL, base, 32768);
    if (!count || count >= 32000) return 1;
    wchar_t *last = wcsrchr(base, L'\\');
    if (!last) return 1;
    *last = 0;
    swprintf(python, 32768, L"%ls\\runtime\\pythonw.exe", base);
    swprintf(script, 32768, L"%ls\\app\\desktop.py", base);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"Tunnel Studio's application files are missing. Run the latest full Tunnel Studio Windows installer again.", L"Tunnel Studio", MB_OK | MB_ICONERROR);
        return 1;
    }
    swprintf(command, 65536, L"\"%ls\" \"%ls\"", python, script);
    STARTUPINFOW si = {0}; si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, base, &si, &pi)) {
        swprintf(error, 1024, L"Windows could not start Tunnel Studio (error %lu). Please include this error number when reporting the problem.", GetLastError());
        MessageBoxW(NULL, error, L"Tunnel Studio", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return 0;
}
