#include <windows.h>

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous,
                   LPSTR command, int show) {
    MessageBoxW(nullptr, L"Hello Windows", L"PortAI Demo", MB_OK);
    return 0;
}
