import 'package:flutter/material.dart';
import 'preview_page.dart';

/// 移动端：在 App 内打开受控 WebView，不再显示黑色占位。
void openPreview(BuildContext context, String url) {
  Navigator.of(
    context,
  ).push(MaterialPageRoute(builder: (_) => PreviewPage(url: url)));
}
