/* 正式预览模式协调器。
 * 保留历史文件名以兼容静态路径，但正式入口不再加载 Gaussian：
 * points = 真实稠密点云；structure = 纯 2.5D 空间结构。 */
(function () {
  'use strict';
  window.__PREVIEW_MODE__ = 'gaussian'; // 仅阻止 viewer.js 抢先自启动。

  var params = new URLSearchParams(location.search);
  var scanId = params.get('scan');
  var mode = params.get('mode') === 'structure' ? 'structure' : 'points';
  var token = params.get('token') || '';
  var headers = token ? { Authorization: 'Bearer ' + token } : {};

  function $(id) { return document.getElementById(id); }
  function setModeUrl(nextMode) {
    var next = new URL(location.href);
    next.searchParams.set('mode', nextMode);
    location.href = next.toString();
  }
  function setRowsVisible(structure) {
    ['walls', 'doors', 'windows', 'objects', 'obstacles', 'labels'].forEach(function (name) {
      var row = $('row-' + name);
      if (row) row.style.display = structure ? 'flex' : 'none';
    });
    $('row-pcd').style.display = structure ? 'none' : 'flex';
    $('row-point-size').style.display = structure ? 'none' : 'flex';
  }
  function fail(message) {
    $('overlay-status').style.display = 'none';
    $('error').style.display = 'block';
    $('error').textContent = message;
    $('bar-outer').style.display = 'none';
    $('btn-retry').style.display = 'block';
  }

  async function main() {
    if (!scanId) { fail('缺少 scan 参数'); return; }
    $('btn-retry').onclick = function () { location.reload(); };
    $('btn-mode-points').onclick = function () { setModeUrl('points'); };
    $('btn-mode-structure').onclick = function () { setModeUrl('structure'); };
    $('btn-mode-points').disabled = mode === 'points';
    $('btn-mode-structure').disabled = mode === 'structure';
    setRowsVisible(mode === 'structure');

    try {
      $('overlay-status').textContent = '获取场景清单';
      $('bar').style.width = '5%';
      var response = await fetch('/api/preview/' + encodeURIComponent(scanId) + '/manifest.json', { headers: headers });
      if (!response.ok) throw new Error('清单加载失败 HTTP ' + response.status + '（请确认已登录）');
      var manifest = await response.json();
      $('scene-title').textContent = manifest.name || ('扫描 #' + scanId);
      if (mode === 'structure') {
        if (!manifest.structure) throw new Error('该扫描尚未生成空间结构结果');
        await window.__startStructureViewer__(manifest, token);
      } else {
        window.__PREVIEW_MODE__ = 'points';
        window.__startPointsViewer__();
      }
    } catch (error) {
      fail(error && error.message ? error.message : String(error));
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main);
  else main();
})();
