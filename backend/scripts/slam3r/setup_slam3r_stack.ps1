<#
  安龄智境新重建栈一键部署：SLAM3R + SpatialLM1.1-Qwen-0.5B

  产物：
    1. 源码：  E:\.PJs\slam3r（SLAM3R，GitHub）
               E:\.PJs\spatiallm（SpatialLM，GitHub，含 Windows flash-attn 禁用补丁）
    2. 环境：  slam3r  conda env（python 3.11 + torch 2.7.1 cu128）
               spatiallm conda env（python 3.11 + torch 2.7.1 cu128 + transformers 4.46
                                    + spconv cu128 + torch-scatter，flash-attn 已禁用）
    3. 权重：  E:\.PJs\models\slam3r_i2p\slam3r_i2p.pth
               E:\.PJs\models\slam3r_l2w\slam3r_l2w.pth
               E:\.PJs\models\SpatialLM1.1-Qwen-0.5B\
    4. 后端：  backend/.env 的 SLAM3R_*/SPATIALLM_* 配置（自动写入）

  说明：权重下载在境内网络下使用 AIFastHub/ModelScope 镜像源；
  HuggingFace 直连不可用时本脚本仍可工作。

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File setup_slam3r_stack.ps1
  可选参数：
    -SkipSources   已克隆过源码时跳过 git clone
    -SkipEnv       已创建过两个 conda 环境时跳过安装
    -SkipWeights   已下载过权重时跳过下载
#>
param(
    [switch]$SkipSources,
    [switch]$SkipEnv,
    [switch]$SkipWeights
)
$ErrorActionPreference = 'Stop'
$deployDir = 'E:\.PJs\deploy'
New-Item -ItemType Directory -Force $deployDir | Out-Null
$log = Join-Path $deployDir 'setup_slam3r_stack.log'
Start-Transcript -Path $log -Force

$conda = 'C:\anaconda3\Scripts\conda.exe'
$slam3rPy = 'C:\Users\Adamz\.conda\envs\slam3r\python.exe'
$spatiallmPy = 'C:\Users\Adamz\.conda\envs\spatiallm\python.exe'

# ---------- 1. 源码 ----------
if (-not $SkipSources) {
    if (-not (Test-Path 'E:\.PJs\slam3r\.git')) {
        Write-Host '=== cloning SLAM3R ==='
        git clone --depth 1 https://github.com/pku-vcl-3dv/SLAM3R.git E:\.PJs\slam3r
    }
    if (-not (Test-Path 'E:\.PJs\spatiallm\.git')) {
        Write-Host '=== cloning SpatialLM ==='
        git clone --depth 1 https://github.com/manycore-research/SpatialLM.git E:\.PJs\spatiallm
    }
    # Windows 补丁：Sonata 编码器禁用 flash-attn（无官方 Windows 轮子，纯 PyTorch 注意力等价）
    $target = 'E:\.PJs\spatiallm\spatiallm\model\spatiallm_qwen.py'
    $text = Get-Content $target -Raw
    if ($text -notmatch 'enable_flash=False') {
        $needle = "                num_bins=point_config[`"num_bins`"],`n            )"
        $replace = "                num_bins=point_config[`"num_bins`"],`n                # Windows patch: disable flash-attn`n                enable_flash=False,`n            )"
        if ($text.Contains($needle)) {
            $text = $text.Replace($needle, $replace)
            Set-Content -Path $target -Value $text -NoNewline
            Write-Host '=== applied spatiallm flash-attn patch ==='
        } else {
            Write-Host '=== WARN: spatiallm patch needle not found (may already be patched) ==='
        }
    } else {
        Write-Host '=== spatiallm already patched ==='
    }
}

# ---------- 2. 环境 ----------
if (-not $SkipEnv) {
    $wheelDir = 'E:\.PJs\deploy\wheels'
    New-Item -ItemType Directory -Force $wheelDir | Out-Null
    $torchWheel = Join-Path $wheelDir 'torch-2.7.1+cu128-cp311-cp311-win_amd64.whl'
    $tvWheel = Join-Path $wheelDir 'torchvision-0.22.1+cu128-cp311-cp311-win_amd64.whl'
    foreach ($entry in @(
        @{ Url = 'https://mirrors.aliyun.com/pytorch-wheels/cu128/torch-2.7.1%2Bcu128-cp311-cp311-win_amd64.whl'; Dest = $torchWheel; Size = 3273066072 },
        @{ Url = 'https://mirrors.aliyun.com/pytorch-wheels/cu128/torchvision-0.22.1%2Bcu128-cp311-cp311-win_amd64.whl'; Dest = $tvWheel; Size = 7638405 }
    )) {
        if (-not ((Test-Path $entry.Dest) -and (Get-Item $entry.Dest).Length -eq $entry.Size)) {
            Write-Host "downloading $($entry.Url)"
            & curl.exe -fL --retry 15 --retry-delay 3 -C - -o $entry.Dest $entry.Url
            if ($LASTEXITCODE -ne 0) { throw "curl failed: $($entry.Url)" }
        }
    }

    if (-not (Test-Path $slam3rPy)) {
        Write-Host '=== creating slam3r env ==='
        & $conda create -y -n slam3r python=3.11 --override-channels -c https://repo.anaconda.com/pkgs/main
    }
    Write-Host '=== slam3r deps ==='
    & $slam3rPy -m pip install $torchWheel $tvWheel
    & $slam3rPy -m pip install numpy==1.26.4 roma gradio matplotlib tqdm opencv-python scipy einops trimesh tensorboard "pyglet<2" "huggingface-hub[torch]>=0.22" viser open3d imageio imageio-ffmpeg scikit-image

    if (-not (Test-Path $spatiallmPy)) {
        Write-Host '=== creating spatiallm env ==='
        & $conda create -y -n spatiallm python=3.11 --override-channels -c https://repo.anaconda.com/pkgs/main
    }
    Write-Host '=== spatiallm deps ==='
    & $spatiallmPy -m pip install $torchWheel $tvWheel
    & $spatiallmPy -m pip install "transformers==4.46.1" "tokenizers==0.20.3" safetensors pandas einops numpy==1.26.4 scipy scikit-learn toml "huggingface_hub>=0.25.0" "rerun-sdk>=0.21.0" shapely bbox terminaltables open3d addict
    & $spatiallmPy -m pip install spconv-cu128
    & $spatiallmPy -m pip install torch_scatter -f https://data.pyg.org/whl/torch-2.7.0+cu128.html

    # 冒烟自检
    & $slam3rPy -c "import torch, cv2, open3d, trimesh, einops, roma, imageio; print('slam3r env OK', torch.__version__, torch.cuda.is_available())"
    & $spatiallmPy -c "import torch, transformers, open3d, rerun, spconv, torch_scatter; print('spatiallm env OK', torch.__version__, torch.cuda.is_available())"
}

# ---------- 3. 权重 ----------
if (-not $SkipWeights) {
    $weightsScript = Join-Path $deployDir 'download_weights.ps1'
    # 该脚本内容见部署文档；这里要求它已存在（幂等重跑）
    if (-not (Test-Path $weightsScript)) {
        throw "缺少权重下载脚本 $weightsScript（请先按部署文档准备）"
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $weightsScript
}

Write-Host '=== 冒烟自检：两个模型均可加载 ==='
& $slam3rPy -c @"
import sys, torch; sys.path.insert(0, r'E:\.PJs\slam3r')
from slam3r.models import Image2PointsModel, Local2WorldModel
i2p = Image2PointsModel(pos_embed='RoPE100', img_size=(224,224), head_type='linear', output_mode='pts3d',
    depth_mode=('exp', float('-inf'), float('inf')), conf_mode=('exp', 1, float('inf')),
    enc_embed_dim=1024, enc_depth=24, enc_num_heads=16, dec_embed_dim=768, dec_depth=12, dec_num_heads=12,
    mv_dec1='MultiviewDecoderBlock_max', mv_dec2='MultiviewDecoderBlock_max', enc_minibatch=11)
l2w = Local2WorldModel(pos_embed='RoPE100', img_size=(224,224), head_type='linear', output_mode='pts3d',
    depth_mode=('exp', float('-inf'), float('inf')), conf_mode=('exp', 1, float('inf')),
    enc_embed_dim=1024, enc_depth=24, enc_num_heads=16, dec_embed_dim=768, dec_depth=12, dec_num_heads=12,
    mv_dec1='MultiviewDecoderBlock_max', mv_dec2='MultiviewDecoderBlock_max', enc_minibatch=11, need_encoder=False)
c1 = torch.load(r'E:\.PJs\models\slam3r_i2p\slam3r_i2p.pth', map_location='cpu')
c2 = torch.load(r'E:\.PJs\models\slam3r_l2w\slam3r_l2w.pth', map_location='cpu')
print(i2p.load_state_dict(c1['model'], strict=False))
print(l2w.load_state_dict(c2['model'], strict=False))
print('SLAM3R models load OK')
"@
& $spatiallmPy -c @"
import sys; sys.path.insert(0, r'E:\.PJs\spatiallm')
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
m = AutoModelForCausalLM.from_pretrained(r'E:\.PJs\models\SpatialLM1.1-Qwen-0.5B', torch_dtype=torch.bfloat16)
print('SpatialLM model load OK', sum(p.numel() for p in m.parameters())/1e6, 'M params')
"@

Stop-Transcript
Write-Host '=== SLAM3R + SpatialLM 栈部署完成 ==='
