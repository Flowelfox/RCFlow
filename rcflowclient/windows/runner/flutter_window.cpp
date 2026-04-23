#include "flutter_window.h"

#include <commctrl.h>
#include <windows.h>

#include <optional>
#include <string>

#include "flutter/generated_plugin_registrant.h"

namespace {

constexpr UINT_PTR kFlutterViewSubclassId = 0xF10A57E1;

// Subclass on Flutter's child HWND. Catches WM_PASTE posted directly to the
// focused HWND by accessibility tools that do paste-via-message instead of
// SendInput Ctrl+V. Forwarded to Dart so the standard paste path runs.
LRESULT CALLBACK FlutterViewSubclassProc(HWND hwnd, UINT msg, WPARAM wparam,
                                         LPARAM lparam, UINT_PTR id,
                                         DWORD_PTR dwRefData) {
  if (msg == WM_PASTE) {
    auto* channel =
        reinterpret_cast<flutter::MethodChannel<flutter::EncodableValue>*>(
            dwRefData);
    if (channel) {
      channel->InvokeMethod("paste", nullptr);
    }
    return 0;
  }
  if (msg == WM_NCDESTROY) {
    RemoveWindowSubclass(hwnd, FlutterViewSubclassProc, id);
  }
  return DefSubclassProc(hwnd, msg, wparam, lparam);
}

std::string Utf8FromWide(const wchar_t* w, int wlen) {
  if (wlen <= 0) return "";
  int n = WideCharToMultiByte(CP_UTF8, 0, w, wlen, nullptr, 0, nullptr, nullptr);
  if (n <= 0) return "";
  std::string out(n, '\0');
  WideCharToMultiByte(CP_UTF8, 0, w, wlen, out.data(), n, nullptr, nullptr);
  return out;
}

// Reads CF_UNICODETEXT from the clipboard as UTF-8. Retries briefly on
// open failure since other clipboard listeners (clipboard managers,
// password managers) compete for the same notification.
std::string ReadClipboardTextUtf8(HWND owner) {
  for (int attempt = 0; attempt < 8; ++attempt) {
    if (OpenClipboard(owner)) {
      std::string out;
      HANDLE h = GetClipboardData(CF_UNICODETEXT);
      if (h) {
        auto* p = static_cast<const wchar_t*>(GlobalLock(h));
        if (p) {
          out = Utf8FromWide(p, static_cast<int>(wcslen(p)));
          GlobalUnlock(h);
        }
      }
      CloseClipboard();
      return out;
    }
    Sleep(2);
  }
  return "";
}

}  // namespace

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  external_paste_channel_ =
      std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
          flutter_controller_->engine()->messenger(),
          "rcflow/external_paste",
          &flutter::StandardMethodCodec::GetInstance());

  HWND view_hwnd = flutter_controller_->view()->GetNativeWindow();
  if (view_hwnd) {
    SetWindowSubclass(view_hwnd, FlutterViewSubclassProc,
                      kFlutterViewSubclassId,
                      reinterpret_cast<DWORD_PTR>(external_paste_channel_.get()));
  }

  // Seed prior clipboard text + sequence so the first WM_CLIPBOARDUPDATE
  // delivers a meaningful previousText to Dart.
  previous_clipboard_text_ = ReadClipboardTextUtf8(GetHandle());
  last_clipboard_seq_ = GetClipboardSequenceNumber();

  // Subscribe to clipboard change notifications. Used by the runner to
  // forward {text, previousText, isOwn, isForeground} to Dart, which runs
  // the restore-detection state machine.
  AddClipboardFormatListener(GetHandle());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  RemoveClipboardFormatListener(GetHandle());
  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
    case WM_CLIPBOARDUPDATE: {
      DWORD current_seq = GetClipboardSequenceNumber();
      // `seqJump = true` if the clipboard moved more than once since our
      // last observation — e.g. Wispr Flow's save/paste/restore happened
      // between two of our message-pump cycles. Forwarded so Dart can
      // refuse to insert text that may not be the dictation result.
      bool seq_jumped =
          last_clipboard_seq_ != 0 && current_seq > last_clipboard_seq_ + 1;
      last_clipboard_seq_ = current_seq;

      HWND owner = GetClipboardOwner();
      bool is_own = false;
      if (owner) {
        DWORD owner_pid = 0;
        GetWindowThreadProcessId(owner, &owner_pid);
        is_own = (owner_pid == GetCurrentProcessId());
      }
      bool is_foreground = (GetForegroundWindow() == hwnd);
      std::string text = ReadClipboardTextUtf8(hwnd);
      if (text.empty()) break;

      std::string previous = previous_clipboard_text_;
      previous_clipboard_text_ = text;

      if (external_paste_channel_) {
        flutter::EncodableMap payload;
        payload[flutter::EncodableValue("text")] =
            flutter::EncodableValue(text);
        payload[flutter::EncodableValue("previousText")] =
            flutter::EncodableValue(previous);
        payload[flutter::EncodableValue("isOwn")] =
            flutter::EncodableValue(is_own);
        payload[flutter::EncodableValue("isForeground")] =
            flutter::EncodableValue(is_foreground);
        payload[flutter::EncodableValue("seqJumped")] =
            flutter::EncodableValue(seq_jumped);
        external_paste_channel_->InvokeMethod(
            "clipboard_changed",
            std::make_unique<flutter::EncodableValue>(payload));
      }
      break;
    }
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
