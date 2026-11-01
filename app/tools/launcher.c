#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,PWSTR arguments,int show) {
    wchar_t folder[32768], python[32768], command[65536];
    DWORD n=GetModuleFileNameW(NULL,folder,32768);
    if(!n || n>=32768)return 1;
    wchar_t *end=wcsrchr(folder,L'\\');if(!end)return 1;*end=0;
    if(swprintf(python,32768,L"%ls\\runtime\\pythonw.exe",folder)<0)return 1;
    if(swprintf(command,65536,L"\"%ls\" \"%ls\\portable_bootstrap.py\"",python,folder)<0)return 1;
    STARTUPINFOW startup={0};startup.cb=sizeof(startup);PROCESS_INFORMATION process={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,folder,&startup,&process)){
        MessageBoxW(NULL,L"ZIP 전체를 압축 해제한 뒤 monitoringbot.exe를 실행하세요. runtime 폴더와 서버 파일이 함께 있어야 합니다.",L"IncidentBridge",MB_OK|MB_ICONERROR);return 1;
    }
    CloseHandle(process.hThread);CloseHandle(process.hProcess);return 0;
}
