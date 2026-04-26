#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>

#include <string>

#include "flutter_window.h"
#include "utils.h"

namespace {

// Write the current exe path into HKCU so that clicking a rcflow:// link
// from the worker's "Add to Client" button (or any other source) launches
// this client. The Inno installer registers the same keys for installed
// builds, but users running `flutter build windows` / `flutter run`
// without going through the installer otherwise see Windows' "You'll need
// a new app to open this rcflow link" popup. Running this on every startup
// is idempotent and always points the scheme at the most recently launched
// client binary — the right default when a user has both an installed and
// a dev build.
void RegisterRcflowUrlScheme() {
  wchar_t exe_path[MAX_PATH];
  DWORD len = ::GetModuleFileNameW(nullptr, exe_path, MAX_PATH);
  if (len == 0 || len >= MAX_PATH) {
    return;
  }

  auto write_key = [](const wchar_t *subkey, const wchar_t *value_name,
                      const std::wstring &value) {
    HKEY key = nullptr;
    LONG status = ::RegCreateKeyExW(HKEY_CURRENT_USER, subkey, 0, nullptr,
                                    REG_OPTION_NON_VOLATILE, KEY_WRITE,
                                    nullptr, &key, nullptr);
    if (status != ERROR_SUCCESS) {
      return;
    }
    ::RegSetValueExW(
        key, value_name, 0, REG_SZ,
        reinterpret_cast<const BYTE *>(value.c_str()),
        static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
    ::RegCloseKey(key);
  };

  write_key(L"Software\\Classes\\rcflow", nullptr, L"URL:RCFlow Protocol");
  write_key(L"Software\\Classes\\rcflow", L"URL Protocol", L"");

  std::wstring icon_value;
  icon_value.append(L"\"").append(exe_path).append(L"\",0");
  write_key(L"Software\\Classes\\rcflow\\DefaultIcon", nullptr, icon_value);

  std::wstring command_value;
  command_value.append(L"\"").append(exe_path).append(L"\" \"%1\"");
  write_key(L"Software\\Classes\\rcflow\\shell\\open\\command", nullptr,
            command_value);
}

}  // namespace

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  // Attach to console when present (e.g., 'flutter run') or create a
  // new console when running with a debugger.
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  RegisterRcflowUrlScheme();

  // Initialize COM, so that it is available for use in the library and/or
  // plugins.
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  if (!window.Create(L"RCFlow", origin, size)) {
    return EXIT_FAILURE;
  }
  window.SetQuitOnClose(true);

  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}
