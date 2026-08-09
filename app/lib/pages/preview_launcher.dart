// 打开 3D 预览：web 端用 window.open 新标签打开渲染器，
// 移动端无 dart:html，由平台实现决定（no-op）。
export 'preview_launcher_web.dart'
    if (dart.library.io) 'preview_launcher_io.dart';
