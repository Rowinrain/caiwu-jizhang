/*
 * 财务记账软件 Windows 启动器。
 * 作用：找到软件自带的 Python 运行环境，用 pythonw.exe（不显示黑色窗口）启动 start.py，然后立即退出。
 * 运行环境的查找顺序：runtime\（Win10/11 版） → runtime-x64\ 或 runtime-x86\（Win7 版，按系统位数选择）。
 * 编译（在 Linux 上用 llvm-mingw 交叉编译为 32 位程序，32/64 位 Windows 都能运行）：
 *   i686-w64-mingw32-windres launcher.rc -O coff -o launcher.res
 *   i686-w64-mingw32-clang -municode -mwindows -O2 -s launcher.c launcher.res -o 财务记账软件.exe
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>

#define TITLE L"财务记账软件"

static int file_exists(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void join(wchar_t *out, size_t n, const wchar_t *dir, const wchar_t *rel) {
    lstrcpynW(out, dir, (int)n);
    lstrcatW(out, rel);
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, LPWSTR cmd, int show) {
    wchar_t dir[MAX_PATH * 2], py[MAX_PATH * 2], script[MAX_PATH * 2], cmdline[MAX_PATH * 6];
    DWORD len = GetModuleFileNameW(NULL, dir, MAX_PATH * 2);
    if (!len) return 1;
    for (DWORD i = len; i > 0; i--) {
        if (dir[i - 1] == L'\\' || dir[i - 1] == L'/') { dir[i] = 0; break; }
    }

    SYSTEM_INFO si;
    GetNativeSystemInfo(&si);
    int is64 = si.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_AMD64 || si.wProcessorArchitecture == 12; /* ARM64 */

    const wchar_t *candidates[3];
    int n = 0;
    candidates[n++] = L"runtime\\pythonw.exe";
    if (is64) candidates[n++] = L"runtime-x64\\pythonw.exe";
    candidates[n++] = L"runtime-x86\\pythonw.exe";

    py[0] = 0;
    for (int i = 0; i < n; i++) {
        join(py, MAX_PATH * 2, dir, candidates[i]);
        if (file_exists(py)) break;
        py[0] = 0;
    }
    join(script, MAX_PATH * 2, dir, L"start.py");
    if (!py[0] || !file_exists(script)) {
        MessageBoxW(NULL, L"找不到软件的运行环境（runtime 文件夹）或 start.py。\n\n"
                          L"请把下载的压缩包完整解压后，再双击“财务记账软件.exe”。\n"
                          L"不要直接在压缩包里双击运行。", TITLE, MB_ICONERROR);
        return 1;
    }

    wsprintfW(cmdline, L"\"%s\" \"%s\"", py, script);
    STARTUPINFOW st;
    PROCESS_INFORMATION pi;
    ZeroMemory(&st, sizeof(st));
    st.cb = sizeof(st);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(py, cmdline, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &st, &pi)) {
        wchar_t msg[512];
        wsprintfW(msg, L"启动失败（错误代码 %lu）。\n\n如果杀毒软件拦截了，请把软件文件夹加入信任。", GetLastError());
        MessageBoxW(NULL, msg, TITLE, MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
