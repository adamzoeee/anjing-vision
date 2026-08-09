// 平台条件导入（web 专用实现）：dart:html 在该文件中是必要依赖
// ignore_for_file: avoid_web_libraries_in_flutter, deprecated_member_use
import 'dart:html' as html;

/// web 端：新标签打开 3D 预览渲染器页面。
void openPreview(String url) {
  html.window.open(url, '_blank');
}
