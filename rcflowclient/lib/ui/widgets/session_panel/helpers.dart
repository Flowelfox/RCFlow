const monthNames = [
  '',
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

String monthAbbr(int month) =>
    (month >= 1 && month <= 12) ? monthNames[month] : '???';

String formatTokens(int tokens) {
  if (tokens >= 1000000) return '${(tokens / 1000000).toStringAsFixed(1)}M';
  if (tokens >= 1000) return '${(tokens / 1000).toStringAsFixed(1)}K';
  return tokens.toString();
}

const terminalStatuses = {'completed', 'failed', 'cancelled'};

bool isTerminalStatus(String status) => terminalStatuses.contains(status);
