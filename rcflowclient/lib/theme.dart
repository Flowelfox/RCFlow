import 'package:flutter/material.dart';

// ============================================================================
// DARK THEME COLORS
// ============================================================================

// Core palette — refined dark with depth
const kBgBase = Color(0xFF0D1117); // GitHub-dark base
const kBgSurface = Color(0xFF161B22); // Cards, input bar
const kBgElevated = Color(0xFF1C2128); // Elevated surfaces
const kBgOverlay = Color(0xFF252B33); // Hover, active states

// Accent
const kAccent = Color(0xFF6366F1); // Indigo-500 — primary action
const kAccentLight = Color(0xFF818CF8); // Indigo-400 — highlights
const kAccentDim = Color(0xFF312E81); // Indigo-900 — subtle backgrounds

// Semantic
const kUserBubble = Color(0xFF1E3A5F); // User message background
const kUserText = Color(0xFF60A5FA); // Blue-400
const kAssistantText = Color(0xFFE5E7EB); // Gray-200
const kToolAccent = Color(0xFFFBBF24); // Amber-400 — tool tags
const kToolBg = Color(0xFF1A1D23); // Tool block background
const kToolOutputText = Color(0xFF9CA3AF); // Gray-400
const kErrorBg = Color(0xFF3B1219); // Error background
const kErrorText = Color(0xFFF87171); // Red-400
const kSuccessBg = Color(0xFF132A1B); // Success tint
const kSuccessText = Color(0xFF4ADE80); // Green-400
const kSummaryBg = Color(0xFF1A1730); // Subtle indigo tint
const kSummaryText = Color(0xFFA5B4FC); // Indigo-300
const kSystemText = Color(0xFF6B7280); // Gray-500
const kDivider = Color(0xFF21262D); // Subtle divider
const kTextPrimary = Color(0xFFE5E7EB); // Gray-200
const kTextSecondary = Color(0xFF9CA3AF); // Gray-400
const kTextMuted = Color(0xFF6B7280); // Gray-500

// ============================================================================
// LIGHT THEME COLORS
// ============================================================================

// Core palette — clean light with good contrast
const kLightBgBase = Color(0xFFFAFAFA); // Light gray base
const kLightBgSurface = Color(0xFFFFFFFF); // White cards, input bar
const kLightBgElevated = Color(0xFFF5F5F5); // Elevated surfaces
const kLightBgOverlay = Color(0xFFE5E5E5); // Hover, active states

// Accent (same as dark for consistency)
const kLightAccent = Color(0xFF6366F1); // Indigo-500 — primary action
const kLightAccentLight = Color(0xFF818CF8); // Indigo-400 — highlights
const kLightAccentDim = Color(0xFFE0E7FF); // Indigo-100 — subtle backgrounds

// Semantic
const kLightUserBubble = Color(
  0xFFDDEAFE,
); // Light blue user message background
const kLightUserText = Color(0xFF2563EB); // Blue-600
const kLightAssistantText = Color(0xFF1F2937); // Gray-800
const kLightToolAccent = Color(0xFFF59E0B); // Amber-500 — tool tags
const kLightToolBg = Color(0xFFF9FAFB); // Tool block background
const kLightToolOutputText = Color(0xFF6B7280); // Gray-500
const kLightErrorBg = Color(0xFFFEE2E2); // Error background
const kLightErrorText = Color(0xFFDC2626); // Red-600
const kLightSuccessBg = Color(0xFFDCFCE7); // Success tint
const kLightSuccessText = Color(0xFF16A34A); // Green-600
const kLightSummaryBg = Color(0xFFEEF2FF); // Subtle indigo tint
const kLightSummaryText = Color(0xFF6366F1); // Indigo-500
const kLightSystemText = Color(0xFF9CA3AF); // Gray-400
const kLightDivider = Color(0xFFE5E7EB); // Subtle divider
const kLightTextPrimary = Color(0xFF1F2937); // Gray-800
const kLightTextSecondary = Color(0xFF6B7280); // Gray-500
const kLightTextMuted = Color(0xFF9CA3AF); // Gray-400

// ============================================================================
// THEME EXTENSIONS
// ============================================================================

/// Custom theme extension for app-specific colors
class AppColors extends ThemeExtension<AppColors> {
  final Color bgBase;
  final Color bgSurface;
  final Color bgElevated;
  final Color bgOverlay;
  final Color accent;
  final Color accentLight;
  final Color accentDim;
  final Color userBubble;
  final Color userText;
  final Color assistantText;
  final Color toolAccent;
  final Color toolBg;
  final Color toolOutputText;
  final Color errorBg;
  final Color errorText;
  final Color successBg;
  final Color successText;
  final Color summaryBg;
  final Color summaryText;
  final Color systemText;
  final Color divider;
  final Color textPrimary;
  final Color textSecondary;
  final Color textMuted;

  const AppColors({
    required this.bgBase,
    required this.bgSurface,
    required this.bgElevated,
    required this.bgOverlay,
    required this.accent,
    required this.accentLight,
    required this.accentDim,
    required this.userBubble,
    required this.userText,
    required this.assistantText,
    required this.toolAccent,
    required this.toolBg,
    required this.toolOutputText,
    required this.errorBg,
    required this.errorText,
    required this.successBg,
    required this.successText,
    required this.summaryBg,
    required this.summaryText,
    required this.systemText,
    required this.divider,
    required this.textPrimary,
    required this.textSecondary,
    required this.textMuted,
  });

  static const dark = AppColors(
    bgBase: kBgBase,
    bgSurface: kBgSurface,
    bgElevated: kBgElevated,
    bgOverlay: kBgOverlay,
    accent: kAccent,
    accentLight: kAccentLight,
    accentDim: kAccentDim,
    userBubble: kUserBubble,
    userText: kUserText,
    assistantText: kAssistantText,
    toolAccent: kToolAccent,
    toolBg: kToolBg,
    toolOutputText: kToolOutputText,
    errorBg: kErrorBg,
    errorText: kErrorText,
    successBg: kSuccessBg,
    successText: kSuccessText,
    summaryBg: kSummaryBg,
    summaryText: kSummaryText,
    systemText: kSystemText,
    divider: kDivider,
    textPrimary: kTextPrimary,
    textSecondary: kTextSecondary,
    textMuted: kTextMuted,
  );

  static const light = AppColors(
    bgBase: kLightBgBase,
    bgSurface: kLightBgSurface,
    bgElevated: kLightBgElevated,
    bgOverlay: kLightBgOverlay,
    accent: kLightAccent,
    accentLight: kLightAccentLight,
    accentDim: kLightAccentDim,
    userBubble: kLightUserBubble,
    userText: kLightUserText,
    assistantText: kLightAssistantText,
    toolAccent: kLightToolAccent,
    toolBg: kLightToolBg,
    toolOutputText: kLightToolOutputText,
    errorBg: kLightErrorBg,
    errorText: kLightErrorText,
    successBg: kLightSuccessBg,
    successText: kLightSuccessText,
    summaryBg: kLightSummaryBg,
    summaryText: kLightSummaryText,
    systemText: kLightSystemText,
    divider: kLightDivider,
    textPrimary: kLightTextPrimary,
    textSecondary: kLightTextSecondary,
    textMuted: kLightTextMuted,
  );

  @override
  ThemeExtension<AppColors> copyWith({
    Color? bgBase,
    Color? bgSurface,
    Color? bgElevated,
    Color? bgOverlay,
    Color? accent,
    Color? accentLight,
    Color? accentDim,
    Color? userBubble,
    Color? userText,
    Color? assistantText,
    Color? toolAccent,
    Color? toolBg,
    Color? toolOutputText,
    Color? errorBg,
    Color? errorText,
    Color? successBg,
    Color? successText,
    Color? summaryBg,
    Color? summaryText,
    Color? systemText,
    Color? divider,
    Color? textPrimary,
    Color? textSecondary,
    Color? textMuted,
  }) {
    return AppColors(
      bgBase: bgBase ?? this.bgBase,
      bgSurface: bgSurface ?? this.bgSurface,
      bgElevated: bgElevated ?? this.bgElevated,
      bgOverlay: bgOverlay ?? this.bgOverlay,
      accent: accent ?? this.accent,
      accentLight: accentLight ?? this.accentLight,
      accentDim: accentDim ?? this.accentDim,
      userBubble: userBubble ?? this.userBubble,
      userText: userText ?? this.userText,
      assistantText: assistantText ?? this.assistantText,
      toolAccent: toolAccent ?? this.toolAccent,
      toolBg: toolBg ?? this.toolBg,
      toolOutputText: toolOutputText ?? this.toolOutputText,
      errorBg: errorBg ?? this.errorBg,
      errorText: errorText ?? this.errorText,
      successBg: successBg ?? this.successBg,
      successText: successText ?? this.successText,
      summaryBg: summaryBg ?? this.summaryBg,
      summaryText: summaryText ?? this.summaryText,
      systemText: systemText ?? this.systemText,
      divider: divider ?? this.divider,
      textPrimary: textPrimary ?? this.textPrimary,
      textSecondary: textSecondary ?? this.textSecondary,
      textMuted: textMuted ?? this.textMuted,
    );
  }

  @override
  ThemeExtension<AppColors> lerp(ThemeExtension<AppColors>? other, double t) {
    if (other is! AppColors) return this;
    return AppColors(
      bgBase: Color.lerp(bgBase, other.bgBase, t)!,
      bgSurface: Color.lerp(bgSurface, other.bgSurface, t)!,
      bgElevated: Color.lerp(bgElevated, other.bgElevated, t)!,
      bgOverlay: Color.lerp(bgOverlay, other.bgOverlay, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      accentLight: Color.lerp(accentLight, other.accentLight, t)!,
      accentDim: Color.lerp(accentDim, other.accentDim, t)!,
      userBubble: Color.lerp(userBubble, other.userBubble, t)!,
      userText: Color.lerp(userText, other.userText, t)!,
      assistantText: Color.lerp(assistantText, other.assistantText, t)!,
      toolAccent: Color.lerp(toolAccent, other.toolAccent, t)!,
      toolBg: Color.lerp(toolBg, other.toolBg, t)!,
      toolOutputText: Color.lerp(toolOutputText, other.toolOutputText, t)!,
      errorBg: Color.lerp(errorBg, other.errorBg, t)!,
      errorText: Color.lerp(errorText, other.errorText, t)!,
      successBg: Color.lerp(successBg, other.successBg, t)!,
      successText: Color.lerp(successText, other.successText, t)!,
      summaryBg: Color.lerp(summaryBg, other.summaryBg, t)!,
      summaryText: Color.lerp(summaryText, other.summaryText, t)!,
      systemText: Color.lerp(systemText, other.systemText, t)!,
      divider: Color.lerp(divider, other.divider, t)!,
      textPrimary: Color.lerp(textPrimary, other.textPrimary, t)!,
      textSecondary: Color.lerp(textSecondary, other.textSecondary, t)!,
      textMuted: Color.lerp(textMuted, other.textMuted, t)!,
    );
  }
}

// ============================================================================
// THEME BUILDERS
// ============================================================================

/// Builds the dark theme for the app
ThemeData buildDarkTheme() {
  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    scaffoldBackgroundColor: kBgBase,
    colorScheme: const ColorScheme.dark(
      primary: kAccent,
      onPrimary: Colors.white,
      surface: kBgSurface,
      onSurface: kTextPrimary,
      error: kErrorText,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: kBgBase,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
    ),
    cardTheme: CardThemeData(
      color: kBgSurface,
      elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      margin: EdgeInsets.zero,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: kBgElevated,
      border: OutlineInputBorder(
        borderSide: BorderSide.none,
        borderRadius: BorderRadius.circular(24),
      ),
      enabledBorder: OutlineInputBorder(
        borderSide: BorderSide.none,
        borderRadius: BorderRadius.circular(24),
      ),
      focusedBorder: OutlineInputBorder(
        borderSide: const BorderSide(color: kAccent, width: 1.5),
        borderRadius: BorderRadius.circular(24),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      hintStyle: const TextStyle(color: kTextMuted, fontSize: 15),
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: kBgSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
    ),
    dividerTheme: const DividerThemeData(color: kDivider, thickness: 1),
    checkboxTheme: CheckboxThemeData(
      fillColor: WidgetStateProperty.resolveWith<Color>((states) {
        if (states.contains(WidgetState.selected)) return kAccent;
        return Colors.transparent;
      }),
      side: const BorderSide(color: kTextSecondary, width: 1.5),
      checkColor: WidgetStateProperty.all(Colors.white),
    ),
    extensions: const [AppColors.dark],
  );
}

/// Builds the light theme for the app
ThemeData buildLightTheme() {
  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
    scaffoldBackgroundColor: kLightBgBase,
    colorScheme: const ColorScheme.light(
      primary: kLightAccent,
      onPrimary: Colors.white,
      surface: kLightBgSurface,
      onSurface: kLightTextPrimary,
      error: kLightErrorText,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: kLightBgBase,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      foregroundColor: kLightTextPrimary,
      iconTheme: IconThemeData(color: kLightTextPrimary),
    ),
    cardTheme: CardThemeData(
      color: kLightBgSurface,
      elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      margin: EdgeInsets.zero,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: kLightBgElevated,
      border: OutlineInputBorder(
        borderSide: BorderSide.none,
        borderRadius: BorderRadius.circular(24),
      ),
      enabledBorder: OutlineInputBorder(
        borderSide: BorderSide.none,
        borderRadius: BorderRadius.circular(24),
      ),
      focusedBorder: OutlineInputBorder(
        borderSide: const BorderSide(color: kLightAccent, width: 1.5),
        borderRadius: BorderRadius.circular(24),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      hintStyle: const TextStyle(color: kLightTextMuted, fontSize: 15),
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: kLightBgSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
    ),
    dividerTheme: const DividerThemeData(color: kLightDivider, thickness: 1),
    checkboxTheme: CheckboxThemeData(
      fillColor: WidgetStateProperty.resolveWith<Color>((states) {
        if (states.contains(WidgetState.selected)) return kLightAccent;
        return Colors.transparent;
      }),
      side: const BorderSide(color: kLightTextSecondary, width: 1.5),
      checkColor: WidgetStateProperty.all(Colors.white),
    ),
    extensions: const [AppColors.light],
  );
}

/// Backward compatibility function (deprecated)
@Deprecated('Use buildDarkTheme() instead')
ThemeData buildAppTheme() => buildDarkTheme();

/// Extension to easily access app colors from BuildContext
extension AppColorsExtension on BuildContext {
  AppColors get appColors => Theme.of(this).extension<AppColors>()!;
}
