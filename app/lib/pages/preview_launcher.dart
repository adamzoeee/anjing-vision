// 打开 3D 预览：web 端用新标签，移动端进入内嵌 WebView。
export 'preview_launcher_web.dart'
    if (dart.library.io) 'preview_launcher_io.dart';
