# 环境搭建记录（Windows）

本文件记录本机（Windows + RTX 5080 Laptop GPU）搭建 GPU 重建环境的完整过程与踩坑记录，
供重装/迁移参考。

## 关键路径

| 组件 | 路径 |
|---|---|
| Python 3.12 | `C:\Users\Adamz\AppData\Local\Programs\Python\Python312\` |
| venv | `backend\.venv\` |
| CUDA Toolkit（zip 免安装） | `C:\Users\Adamz\CUDA\v12.8\merged\` |
| MSVC Build Tools | `C:\Users\Adamz\BuildTools\` |
| ffmpeg | `C:\Program Files\ffmpeg\bin\ffmpeg.exe` |

## 运行 GPU 任务必须的环境变量

```bash
export CUDA_HOME='C:/Users/Adamz/CUDA/v12.8/merged'
export CUDA_PATH='C:/Users/Adamz/CUDA/v12.8/merged'
export PATH="C:/Users/Adamz/CUDA/v12.8/merged/bin:C:/Users/Adamz/BuildTools/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64:$PATH"
```

## 踩坑记录

1. **open3d 不支持 Python 3.14/3.13**——必须 Python 3.12（`pip index versions open3d` 在 3.13/3.14 均 No matching distribution）。
2. **torch 必须 cu128**——RTX 5080 是 Blackwell sm_120，cu124/cu126 构建不支持。
3. **Python 安装器在 MSYS bash 下失败（exit 0x2/1314）**——`LOCALAPPDATA` 被 bash 转为正斜杠 `C:/...`，MSI 拼接 Package Cache 路径出错。解决：`export LOCALAPPDATA='C:\Users\Adamz\AppData\Local'`（反斜杠）后再启动安装器。
4. **CUDA network installer 在本机无法运行（0xe0e00019）**——改用 NVIDIA redist 的 zip 组件（免安装）：
   - 元数据：`https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.8.0.json`
   - 组件：`cuda_nvcc` / `cuda_cudart` / `cuda_cccl` / `cuda_nvrtc` 的 `windows-x86_64-*-archive.zip`
   - 解压后合并 bin/include/lib 到统一目录，并创建 `version.json`（内容 `{"cuda": {"version": "12.8.61"}}`）——gsplat 依赖它。
5. **w64devkit bash 的挂载陷阱**：该 bash 的根 `/` 不是 Windows 系统盘，`/c/...` 实际映射到 `E:\c\...`；访问真实 C 盘要用 `C:/...` 前缀（df 输出为准）。`ls /c/Windows` 不存在即说明挂载异常。
6. **VS 2022 Community 缺 C++ 工具链**（vcvars64.bat 不存在）——用 Build Tools 引导器装 VCTools 工作负载：
   `vs_BuildTools.exe --quiet --wait --norestart --installPath C:\Users\Adamz\BuildTools --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended`
7. **gsplat 1.5.3 是源码分发**，首次 import 时 JIT 编译（需 nvcc + MSVC）。Windows 编译需打补丁：
   - `site-packages/gsplat/cuda/_backend.py` 第 177 行：`extra_cflags` 去掉 `"-Wno-attributes"`（GCC 参数，MSVC 报 D8021），并按平台条件化：
     `extra_cflags = [opt_level, "-Wno-attributes"] if sys.platform != "win32" else [opt_level]`
   - 需 `import sys`
   - 编译产物缓存：`%LOCALAPPDATA%\torch_extensions\gsplat_cuda`
8. **gsplat 1.5.3 API 与旧版不同**（训练器已按新 API 实现）：
   `rasterization(means, quats, scales, opacities(N,), colors(N,(d+1)^2,3), viewmats, Ks, width, height, sh_degree, packed)`
9. **pycolmap 4.1.1**：`incremental_mapping` 返回 dict（失败时空 dict，键 `reconstruction`）；位姿用 `img.cam_from_world`（Rigid3d）→ `rotation().matrix()` / `translation()`。

## 验证命令

```bash
# 全套环境验证
export CUDA_HOME='C:/Users/Adamz/CUDA/v12.8/merged' CUDA_PATH='C:/Users/Adamz/CUDA/v12.8/merged'
export PATH="C:/Users/Adamz/CUDA/v12.8/merged/bin:$PATH"
cd backend
.venv/Scripts/python -c "import open3d, torch, pycolmap, gsplat, cv2; print(open3d.__version__, torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
