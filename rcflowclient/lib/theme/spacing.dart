/// Centralised spacing tokens for layouts and components.
///
/// The widget tree previously sprinkled raw `EdgeInsets.all(kSpace2)`,
/// `SizedBox(width: 12)`, `padding: EdgeInsets.symmetric(horizontal: kSpace4)`
/// across ~60 files with no shared source of truth. The constants below
/// give every padding, gap, and inset a name so we can adjust spacing
/// globally and audit consistency.
///
/// The scale is intentionally small (four steps + two specials). Reach
/// for the named token instead of hardcoding a number — if you find
/// yourself wanting `5.0` or `13.0`, the visual difference from the
/// nearest token is rarely meaningful and the inconsistency adds up.
library;

// Base step: a 4-pt grid (Material guideline).
const double kSpace1 = 4.0;
const double kSpace2 = 8.0;
const double kSpace3 = 12.0;
const double kSpace4 = 16.0;
const double kSpace5 = 24.0;
const double kSpace6 = 32.0;

// Semantic aliases for common cases. Prefer these over the raw step
// when the context is one of: tight padding, default padding, etc.
const double kPadCompact = kSpace2; // dense lists, chips
const double kPadDefault = kSpace3; // most components
const double kPadComfortable = kSpace4; // dialog/section content

// Common gap widths between sibling widgets.
const double kGapInline = kSpace1; // icon + label
const double kGapTight = kSpace2; // related controls in a row
const double kGapRelaxed = kSpace3; // grouped widgets

// Common radius for cards / pills (kept here so spacing.dart is the
// single import for layout primitives).
const double kRadiusSmall = 6.0;
const double kRadiusMedium = 10.0;
const double kRadiusLarge = 14.0;
