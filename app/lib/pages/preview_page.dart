import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

class PreviewPage extends StatefulWidget {
  final String url;

  const PreviewPage({super.key, required this.url});

  @override
  State<PreviewPage> createState() => _PreviewPageState();
}

class _PreviewPageState extends State<PreviewPage> {
  late final WebViewController _controller;
  int _progress = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF101418))
      ..setNavigationDelegate(
        NavigationDelegate(
          onProgress: (value) => setState(() => _progress = value),
          onPageFinished: (_) => setState(() => _progress = 100),
          onWebResourceError: (error) {
            if (error.isForMainFrame == true) {
              setState(() => _error = '3D 预览加载失败：${error.description}');
            }
          },
        ),
      )
      ..loadRequest(Uri.parse(widget.url));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('3D 场景预览')),
      body: Stack(
        children: [
          if (_error == null)
            WebViewWidget(controller: _controller)
          else
            Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_error!, textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: () {
                      setState(() => _error = null);
                      _controller.reload();
                    },
                    child: const Text('重试'),
                  ),
                ],
              ),
            ),
          if (_progress < 100) LinearProgressIndicator(value: _progress / 100),
        ],
      ),
    );
  }
}
