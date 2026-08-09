import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'upload_page.dart';

class CapturePage extends StatefulWidget {
  final Project project;
  final Scan scan;
  const CapturePage({super.key, required this.project, required this.scan});

  @override
  State<CapturePage> createState() => _CapturePageState();
}

class _ReferenceInput {
  String objectType;
  String dimension;
  final TextEditingController meters = TextEditingController();

  _ReferenceInput(this.objectType, this.dimension);
  void dispose() => meters.dispose();
}

class _CapturePageState extends State<CapturePage> {
  static const _objects = {
    'door': '门',
    'bed': '床',
    'sofa': '沙发',
    'table': '桌子',
    'cabinet': '柜子',
  };
  static const _dimensions = {'length': '长度', 'width': '宽度', 'height': '高度'};
  static const _objectDimensions = {
    'door': ['height', 'width'],
    'bed': ['length', 'width', 'height'],
    'sofa': ['length', 'width', 'height'],
    'table': ['length', 'width', 'height'],
    'cabinet': ['height', 'width', 'length'],
  };
  static const _tips = [
    '1. 一个视频只拍一个房间，从门口开始',
    '2. 沿房间边缘缓慢移动一圈，不要站在原地旋转',
    '3. 覆盖地面、墙角、门框、家具，并保持画面重叠',
    '4. 避免快速甩动、逆光、镜面和长时间拍摄纯白墙',
    '5. 最后回到起点；建议录制 1～3 分钟',
  ];

  final List<_ReferenceInput> _references = [
    _ReferenceInput('door', 'height'),
    _ReferenceInput('bed', 'length'),
  ];
  bool _saving = false;

  @override
  void dispose() {
    for (final item in _references) {
      item.dispose();
    }
    super.dispose();
  }

  Future<void> _pickVideo(ImageSource source) async {
    final values = <Map<String, dynamic>>[];
    for (final item in _references) {
      final meters = double.tryParse(item.meters.text.trim());
      if (meters == null || meters <= 0.1 || meters > 20) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('请填写 0.1～20 米之间的真实尺寸')));
        return;
      }
      values.add({
        'object_type': item.objectType,
        'dimension': item.dimension,
        'meters': meters,
      });
    }
    setState(() => _saving = true);
    try {
      await context.read<ApiClient>().setReferenceMeasurements(
        widget.scan.id,
        values,
      );
      final video = await ImagePicker().pickVideo(
        source: source,
        maxDuration: const Duration(minutes: 5),
      );
      if (video == null || !mounted) return;
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => UploadPage(scan: widget.scan, file: video),
        ),
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('保存参考尺寸失败：$error')));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _referenceRow(int index) {
    final item = _references[index];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                initialValue: item.objectType,
                decoration: const InputDecoration(labelText: '参考物'),
                items: _objects.entries
                    .map(
                      (entry) => DropdownMenuItem(
                        value: entry.key,
                        child: Text(entry.value),
                      ),
                    )
                    .toList(),
                onChanged: (value) => setState(() {
                  item.objectType = value!;
                  final supported = _objectDimensions[value]!;
                  if (!supported.contains(item.dimension)) {
                    item.dimension = supported.first;
                  }
                }),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: DropdownButtonFormField<String>(
                key: ValueKey('${item.objectType}-${item.dimension}'),
                initialValue: item.dimension,
                decoration: const InputDecoration(labelText: '尺寸方向'),
                items: _objectDimensions[item.objectType]!
                    .map(
                      (dimension) => DropdownMenuItem(
                        value: dimension,
                        child: Text(_dimensions[dimension]!),
                      ),
                    )
                    .toList(),
                onChanged: (value) => item.dimension = value!,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: item.meters,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: const InputDecoration(
                  labelText: '米',
                  hintText: '2.00',
                ),
              ),
            ),
            if (_references.length > 2)
              IconButton(
                onPressed: () =>
                    setState(() => _references.removeAt(index).dispose()),
                icon: const Icon(Icons.remove_circle_outline),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('采集：${widget.project.name}')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Icon(Icons.view_in_ar, size: 64, color: Colors.teal),
          const Text(
            '先填写 2～3 个真实尺寸',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const Text('系统会用多个参考物校准统一米制比例；无需放置 A4 纸。'),
          const SizedBox(height: 8),
          for (var index = 0; index < _references.length; index++)
            _referenceRow(index),
          if (_references.length < 3)
            TextButton.icon(
              onPressed: () => setState(
                () => _references.add(_ReferenceInput('table', 'length')),
              ),
              icon: const Icon(Icons.add),
              label: const Text('增加第 3 个参考尺寸'),
            ),
          const Divider(height: 28),
          const Text(
            '拍摄引导',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          ..._tips.map(
            (tip) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Text(tip),
            ),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _saving ? null : () => _pickVideo(ImageSource.camera),
            icon: _saving
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.fiber_manual_record),
            label: const Text('保存尺寸并开始录制'),
          ),
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _saving ? null : () => _pickVideo(ImageSource.gallery),
            icon: const Icon(Icons.video_library_outlined),
            label: const Text('保存尺寸并选择已有视频'),
          ),
        ],
      ),
    );
  }
}
