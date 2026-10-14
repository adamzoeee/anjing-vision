# 家庭点云隐患 Demo 与三维渲染版交付

两个独立演示均已附带生成结果，不需要重新训练、SpatialLM 推理或安装 Blender 就能预览。
它们是合成家庭场景，不是实拍重建，也不是正式住宅安全评估。

## 从仓库启动

在仓库根目录打开两个终端（Python 3 标准库即可）：

```text
python -m http.server 8095 --bind 127.0.0.1 --directory backend/data/demos/house_demo_risk_20261013
python -m http.server 8096 --bind 127.0.0.1 --directory backend/data/demos/house_render_20261014_final
```

- http://127.0.0.1:8095/：点云、房间切换、生成真值/SpatialLM 结果、几何建议。
- http://127.0.0.1:8096/：实体三维预览、剖墙、6 张离线渲染图和导出文件。
- 必须使用 HTTP 服务，不要双击 HTML。端口占用时选择其他端口；渲染页返回点云版的链接默认指向 8095。

## 已提交数据

- `backend/data/demos/house_demo_risk_20261013/`：独立房间和整屋 PCD/PLY、网页点云缓冲区、原始模型输出、几何分析和静态预览。
- `backend/data/demos/house_demo_risk_20261013.zip`：点云版完整打包。
- `backend/data/demos/house_render_20261014_final/`：FBX、GLB、glTF/bin、OBJ/MTL、BLEND、6 张 PNG、来源记录、验证结果和静态预览。
- 其中 `house_c4d_bundle.zip`：可编辑模型及图片完整打包。

`backend/data/` 默认仍被忽略；这次仅明确提交上述两个合成演示目录和点云 ZIP，不包含真实扫描、视频、数据库、凭据、模型权重、虚拟环境、Blender 下载包或测试缓存。

## 验收及限制

- 相关回归：65 passed；点云 preview chunks 检查通过。
- 点云版：五房间 SpatialLM 均完成，20 个实际识别对象，有漏检；生成真值不冒充检测结果。
- 渲染版：45 个预设布局对象；391 个导出网格、21 个材质、6 个相机。
- FBX/GLB/OBJ Blender 回读通过；FBX/GLB 的 45 个家具完整变换通过核对。
- C4D 未在本机内验收。优先 FBX，导入后检查 0.9 m 门宽；材质和区域光可能需要调整。
- 渲染规格中绝对路径是生成时的来源记录，不影响已打包页面和模型。重新生成时对当前仓库路径执行 prepare，不直接复用旧机器的来源路径。
- 原 8094 lived 版本没有删除，但本次只打包指定的 risk 和 render 结果；其他版本可通过生成脚本重建。

实现与复现详情：
- [点云隐患版](house-pointcloud-risk-demo.md)
- [渲染与 C4D 导出](house-render-export.md)
