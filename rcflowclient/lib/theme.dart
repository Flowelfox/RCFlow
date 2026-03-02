import 'package:flutter/material.dart';

// Core palette — refined dark with depth
const kBgBase = Color(0xFF0D1117);       // GitHub-dark base
const kBgSurface = Color(0xFF161B22);    // Cards, input bar
const kBgElevated = Color(0xFF1C2128);   // Elevated surfaces
const kBgOverlay = Color(0xFF252B33);    // Hover, active states

// Accent
const kAccent = Color(0xFF6366F1);       // Indigo-500 — primary action
const kAccentLight = Color(0xFF818CF8);  // Indigo-400 — highlights
const kAccentDim = Color(0xFF312E81);    // Indigo-900 — subtle backgrounds

// Semantic
const kUserBubble = Color(0xFF1E3A5F);   // User message background
const kUserText = Color(0xFF60A5FA);     // Blue-400
const kAssistantText = Color(0xFFE5E7EB);// Gray-200
const kToolAccent = Color(0xFFFBBF24);   // Amber-400 — tool tags
const kToolBg = Color(0xFF1A1D23);       // Tool block background
const kToolOutputText = Color(0xFF9CA3AF);// Gray-400
const kErrorBg = Color(0xFF3B1219);      // Error background
const kErrorText = Color(0xFFF87171);    // Red-400
const kSuccessBg = Color(0xFF132A1B);    // Success tint
const kSuccessText = Color(0xFF4ADE80);  // Green-400
const kSummaryBg = Color(0xFF1A1730);   // Subtle indigo tint
const kSummaryText = Color(0xFFA5B4FC); // Indigo-300
const kSystemText = Color(0xFF6B7280);   // Gray-500
const kDivider = Color(0xFF21262D);      // Subtle divider
const kTextPrimary = Color(0xFFE5E7EB);  // Gray-200
const kTextSecondary = Color(0xFF9CA3AF);// Gray-400
const kTextMuted = Color(0xFF6B7280);    // Gray-500

ThemeData buildAppTheme() {
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
  );
}
