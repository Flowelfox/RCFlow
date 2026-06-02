part of 'input_area.dart';

class _MentionItem extends StatelessWidget {
  final String name;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _MentionItem({
    required this.name,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: kSpace3, vertical: kSpace2),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(
              Icons.folder_rounded,
              size: 16,
              color: context.appColors.textMuted,
            ),
            const SizedBox(width: 8),
            Expanded(child: _buildHighlightedName(context)),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}

class _ToolMentionItem extends StatelessWidget {
  final String name;
  final String description;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _ToolMentionItem({
    required this.name,
    required this.description,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: kSpace3, vertical: kSpace2),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(
              Icons.build_rounded,
              size: 16,
              color: context.appColors.textMuted,
            ),
            SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  Text(
                    description,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}

class _FileMentionItem extends StatelessWidget {
  final String fileName;
  final String filePath;
  final String fileExtension;
  final bool isText;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _FileMentionItem({
    required this.fileName,
    required this.filePath,
    required this.fileExtension,
    required this.isText,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: kSpace3, vertical: kSpace2),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(
              _iconForExtension(fileExtension),
              size: 16,
              color: context.appColors.textMuted,
            ),
            SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  Text(
                    filePath,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            if (!isText)
              Padding(
                padding: EdgeInsets.only(left: 6),
                child: Tooltip(
                  message: 'Binary file (metadata only)',
                  child: Icon(
                    Icons.visibility_off_rounded,
                    size: 12,
                    color: context.appColors.textMuted,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  static IconData _iconForExtension(String ext) {
    final e = ext.toLowerCase();
    if (const [
      '.py',
      '.js',
      '.ts',
      '.dart',
      '.java',
      '.cpp',
      '.c',
      '.go',
      '.rs',
      '.rb',
      '.php',
      '.swift',
      '.kt',
    ].contains(e)) {
      return Icons.code_rounded;
    }
    if (const ['.md', '.txt', '.rst', '.log'].contains(e)) {
      return Icons.description_rounded;
    }
    if (const ['.json', '.yaml', '.yml', '.toml', '.xml'].contains(e)) {
      return Icons.data_object_rounded;
    }
    if (const ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'].contains(e)) {
      return Icons.image_rounded;
    }
    if (e == '.pdf') return Icons.picture_as_pdf_rounded;
    return Icons.insert_drive_file_rounded;
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        fileName,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = fileName.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        fileName,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: fileName.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: fileName.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < fileName.length)
            TextSpan(
              text: fileName.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}

/// A slim bar shown above the input row when a subprocess (Claude Code, Codex,
/// etc.) is running. Displays the subprocess name, working directory, current
/// tool, and a kill button.
