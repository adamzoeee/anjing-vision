# Scan46 来源追踪式点云纠错

## 结论

当前可发布改动只包含一项：撤回 727 个经原始第 140 帧像素谱系证明属于窗框内侧、但历史上误归入窗帘组件的点。该改动消除正视与斜视中的上部锯齿亮条，不推断缺失墙面，也不混合不同视频中的软物体状态。

R1、R2、R5 与未观测墙面继续保持未知。R3 床品表面虽有同一连续片段的邻帧支持，但转换为点云后仍呈现不完整窄条，因此构建脚本默认不发布；只能通过显式 `--include-observed-r3` 用于研究对比。

## 构建

四个数据包必须按 `scan46-window-black-handoff.md` 的顺序部署，然后执行：

```powershell
cd backend
python scripts/build_scan46_source_traced_repair.py
```

确认固定视角后，正式部署使用：

```powershell
python scripts/build_scan46_source_traced_repair.py --accept
```

`--accept` 只允许默认纠错模式；如果启用了研究性的 R3 表面，脚本会拒绝切换正式选择。

默认输出：

- `data/work/46/postprocess/local_gaps_source_traced_repair.ply`
- `data/work/46/postprocess/local_gaps_source_traced_repair.visual.json`
- `data/work/46/postprocess/local_gaps_source_traced_repair.audit.json`
- front / oblique / overview / overhead 四个 A/B 对比图

脚本不会自动修改 `preview_selection.json`。只有人工确认固定视角与实际 Viewer 均提升后，才可切换正式选择。

## 验收约束

- 源点云 SHA-256 必须为 `8748b641065cae16f62f8ab9c6a56c03c3786a6f6b966b380481d9064990e4a9`。
- 被撤回点必须来自精确源像素映射，并且数量必须恰好为 727。
- 其余 1,029,669 个基线点的坐标与 RGB 必须逐点保持不变。
- 默认模式不得加入 R1/R2/R3/R5 或假墙点。
- 研究模式中的新增点必须至少填补一个固定视角空洞，并且不得改变任何既有非黑像素。
