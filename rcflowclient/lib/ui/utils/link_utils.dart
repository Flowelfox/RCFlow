import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

/// Opens [href] in an external application when the Ctrl modifier is held.
///
/// Intended as the [MarkdownBody.onTapLink] callback for session chat bubbles.
/// Taps without Ctrl held are silently ignored, preserving normal text
/// interaction (selection, cursor placement, etc.).
///
/// The [launcher] parameter is injectable for testing; defaults to the
/// real [launchUrl].
void openLinkOnCtrlClick(
  String text,
  String? href,
  String title, {
  bool? isCtrlPressed,
  Future<bool> Function(Uri, {LaunchMode mode})? launcher,
}) {
  if (href == null) return;
  final ctrl = isCtrlPressed ?? HardwareKeyboard.instance.isControlPressed;
  if (!ctrl) return;
  final uri = Uri.tryParse(href);
  if (uri == null) return;
  final open = launcher ?? launchUrl;
  open(uri, mode: LaunchMode.externalApplication);
}
