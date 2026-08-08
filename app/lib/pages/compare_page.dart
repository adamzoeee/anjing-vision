import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';

class ComparePage extends StatefulWidget {
  final int beforeScanId;
  final int afterScanId;
  const ComparePage({
    super.key,
    required this.beforeScanId,
    required this.afterScanId,
  });
  @override
  State<ComparePage> createState() => _ComparePageState();
}

class _ComparePageState extends State<ComparePage> {
  Map<String, dynamic>? _data;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await context.read<ApiClient>()
          .compare(widget.beforeScanId, widget.afterScanId);
      if (!mounted) return;
      setState(() { _data = d; _error = null; });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _data;
    return Scaffold(
      appBar: AppBar(title: const Text('改造前后对比')),
      body: _error != null
          ? Center(child: Text(_error!))
          : d == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(padding: const EdgeInsets.all(16), children: [
                  Center(child: Text(
                    '评分变化：${(d['before'] as Map)['score']} → ${(d['after'] as Map)['score']}'
                    '（${d['score_delta'] >= 0 ? '+' : ''}${d['score_delta']}）',
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.bold,
                      color: (d['score_delta'] as num) >= 0 ? Colors.green : Colors.red))),
                  const SizedBox(height: 16),
                  Text('改造前风险', style: Theme.of(context).textTheme.titleMedium),
                  ..._riskTiles(d['before']),
                  const Divider(),
                  Text('改造后风险', style: Theme.of(context).textTheme.titleMedium),
                  ..._riskTiles(d['after']),
                ]));
  }

  List<Widget> _riskTiles(dynamic side) {
    final risks = (side as Map)['risks'] as List? ?? [];
    if (risks.isEmpty) {
      return const [Padding(padding: EdgeInsets.all(8), child: Text('无风险项'))];
    }
    return risks.map((r) {
      final m = r as Map;
      return ListTile(
        title: Text('${m['name']} · ${m['level']}'),
        subtitle: Text(m['measure']?.toString() ?? ''),
      );
    }).toList();
  }
}
