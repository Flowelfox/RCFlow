#ifndef RUNNER_FLUTTER_WINDOW_H_
#define RUNNER_FLUTTER_WINDOW_H_

#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <flutter/method_channel.h>
#include <flutter/standard_method_codec.h>

#include <memory>
#include <string>

#include "win32_window.h"

// A window that does nothing but host a Flutter view.
class FlutterWindow : public Win32Window {
 public:
  // Creates a new FlutterWindow hosting a Flutter view running |project|.
  explicit FlutterWindow(const flutter::DartProject& project);
  virtual ~FlutterWindow();

 protected:
  // Win32Window:
  bool OnCreate() override;
  void OnDestroy() override;
  LRESULT MessageHandler(HWND window, UINT const message, WPARAM const wparam,
                         LPARAM const lparam) noexcept override;

 private:
  // The project to run.
  flutter::DartProject project_;

  // The Flutter instance hosted by this window.
  std::unique_ptr<flutter::FlutterViewController> flutter_controller_;

  // Notifies Dart when WM_PASTE arrives so accessibility/dictation tools
  // (e.g. Wispr Flow) that paste via the standard message instead of a
  // synthesized Ctrl+V keystroke can still insert text into the app.
  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>>
      external_paste_channel_;

  // Most recent clipboard text observed in WM_CLIPBOARDUPDATE. Forwarded to
  // Dart alongside each new event as `previousText` so the restore detector
  // can match against prior clipboard contents directly — needed because
  // Wispr Flow's save/paste/restore cycle generates two events (write Y,
  // restore X) and we must drop the X.
  std::string previous_clipboard_text_;
  DWORD last_clipboard_seq_ = 0;
};

#endif  // RUNNER_FLUTTER_WINDOW_H_
