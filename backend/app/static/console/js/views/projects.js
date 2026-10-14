/* 项目管理工作台：KPI 概览 + 项目卡片网格 */

import { api } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import {
  esc, toast, countUp, openModal, blueprintSVG, timeAgo, fmtScore, scoreTone,
  emptyState,
} from '../ui.js';
import { scoreRingHTML, mountRings } from '../charts.js';
import { navigate } from '../app.js';

export async function projectsView(ctx) {
  ctx.title([{ label: '项目工作台', current: true }]);
  ctx.actions.innerHTML = '';

  const newBtn = document.createElement('button');
  newBtn.className = 'btn btn-primary';
  newBtn.innerHTML = `${icon('plus', 16)} 新建项目`;
  newBtn.addEventListener('click', () => openCreateProject(load));
  ctx.actions.appendChild(newBtn);

  ctx.host.innerHTML = `
    <div class="view-projects">
      <div class="kpi-strip">
        ${[0, 1, 2, 3].map(() => `<div class="kpi-tile glass"><div class="skeleton-line shimmer" style="width:42%"></div><div class="skeleton-line shimmer" style="width:64%;height:26px"></div></div>`).join('')}
      </div>
      <div class="view-scroll">
        <div class="projects-grid">
          ${[0, 1, 2].map(() => `<div class="glass skeleton-block shimmer" style="height:252px"></div>`).join('')}
        </div>
      </div>
    </div>`;

  await load();

  async function load() {
    const projects = await api.listProjects();
    const scanLists = await Promise.all(
      projects.map((project) => api.listScans(project.id).catch(() => [])),
    );
    const allScans = scanLists.flat();
    const doneScans = allScans.filter((scan) => scan.status === 'done');
    const reportEntries = await Promise.all(
      doneScans.slice(0, 12).map(async (scan) => {
        try { return [scan.id, await api.getReport(scan.id)]; }
        catch { return [scan.id, null]; }
      }),
    );
    const reportByScan = new Map(reportEntries);
    const scores = reportEntries.map(([, report]) => report?.score).filter((score) => typeof score === 'number');
    const avgScore = scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;

    const activeCount = allScans.filter((scan) => scan.status !== 'done' && scan.status !== 'failed').length;
    render({ projects, scanLists, reportByScan, avgScore, activeCount, scores });
  }

  function render({ projects, scanLists, reportByScan, avgScore, activeCount, scores }) {
    const totalScans = scanLists.flat().length;
    const doneCount = scanLists.flat().filter((scan) => scan.status === 'done').length;
    const kpis = [
      { name: '项目总数', value: projects.length, unit: '个', iconName: 'folder', foot: '按机构隔离管理' },
      { name: '房间总数', value: totalScans, unit: '间', iconName: 'home', foot: activeCount ? `${activeCount} 间处理中` : '全部就绪' },
      { name: '已完成评估', value: doneCount, unit: '间', iconName: 'checkCircle', foot: `覆盖 ${totalScans ? Math.round((doneCount / totalScans) * 100) : 0}% 房间` },
      { name: '平均综合评分', value: avgScore, unit: '分', iconName: 'gauge', foot: scores.length ? `基于 ${scores.length} 间已完成房间` : '暂无评分数据', decimals: 1 },
    ];
    const grid = ctx.host.querySelector('.view-projects');
    grid.innerHTML = `
      <div class="kpi-strip">
        ${kpis.map((kpi, index) => `
          <div class="kpi-tile glass rise" style="--i:${index}">
            <div class="k-top">
              <div class="k-icon">${icon(kpi.iconName, 17)}</div>
              <div class="k-name">${kpi.name}</div>
            </div>
            <div class="k-value"><span class="kpi-count" data-value="${kpi.value ?? ''}" data-decimals="${kpi.decimals || 0}">—</span><span class="k-unit">${kpi.unit}</span></div>
            <div class="k-foot">${esc(kpi.foot)}</div>
          </div>`).join('')}
      </div>
      <div class="view-scroll">
        ${projects.length ? `
        <div class="projects-grid">
          ${projects.map((project, index) => projectCard(project, scanLists[index], reportByScan, index)).join('')}
          <div class="new-project-card" id="card-new-project" role="button" tabindex="0">
            <div class="np-inner">
              <div class="np-plus">${icon('plus', 20)}</div>
              <div>新建项目</div>
              <div style="font-size:12px;color:var(--text-4)">为一处住宅建立评估项目</div>
            </div>
          </div>
        </div>` : `
        <div class="glass panel">
          ${emptyState({
            iconName: 'folder',
            title: '还没有项目',
            desc: '创建第一个项目，上传房间视频，系统将自动完成三维重建与风险评估。',
            action: '<button class="btn btn-primary" id="btn-empty-new">新建项目</button>',
          })}
        </div>`}
      </div>`;

    ctx.host.querySelectorAll('.kpi-count').forEach((el) => {
      const raw = el.dataset.value;
      if (raw === '' || raw === 'null') { el.textContent = '—'; return; }
      countUp(el, Number(raw), { decimals: Number(el.dataset.decimals || 0), duration: 1000 });
    });
    ctx.host.querySelectorAll('.project-card').forEach((card) => {
      const go = () => navigate(`#/projects/${card.dataset.id}`);
      card.addEventListener('click', go);
      card.addEventListener('keydown', (event) => { if (event.key === 'Enter') go(); });
    });
    ctx.host.querySelector('#card-new-project')?.addEventListener('click', () => openCreateProject(load));
    ctx.host.querySelector('#btn-empty-new')?.addEventListener('click', () => openCreateProject(load));
    mountRings(ctx.host);
  }

  function projectCard(project, scans, reportByScan, index) {
    const done = scans.filter((scan) => scan.status === 'done');
    const latest = done[0];
    const score = latest ? reportByScan.get(latest.id)?.score ?? null : null;
    const lastUpdate = scans.length ? timeAgo(scans[0].created_at || '') : '';
    return `
      <div class="project-card glass rise" style="--i:${index + 4}" data-id="${project.id}" role="button" tabindex="0">
        <div class="pc-cover">
          ${blueprintSVG(project.id)}
          <div class="pc-cover-fade"></div>
          <div class="pc-score">${scoreRingHTML({ score, size: 76, stroke: 6, label: '评分' })}</div>
        </div>
        <div class="pc-body">
          <div class="pc-title">
            <h3>${esc(project.name)}</h3>
            <span class="badge ${done.length ? 'ok' : 'info'}"><span class="dot"></span>${done.length ? '已有评估' : '待评估'}</span>
          </div>
          <div class="pc-addr">${esc(project.address || '未填写地址')}</div>
          <div class="pc-meta">
            <span class="m-item">${icon('home', 14)} ${scans.length} 间房间</span>
            <span class="m-item">${icon('checkCircle', 14)} ${done.length} 间已完成</span>
          </div>
          <div class="pc-foot">
            ${icon('clock', 13)} ${lastUpdate ? `最近更新 ${esc(lastUpdate)}` : '尚未创建房间'}
            <span class="grow"></span>
            进入工作台 ${icon('arrowRight', 14)}
          </div>
        </div>
      </div>`;
  }
}

function openCreateProject(onCreated) {
  openModal({
    title: '新建项目',
    desc: '为一处住宅建立适老化安全评估项目。',
    body: `
      <div class="col gap-4">
        <div class="field">
          <label for="np-name">项目名称 *</label>
          <input class="input" id="np-name" maxlength="120" placeholder="例如：张爷爷家适老化评估">
        </div>
        <div class="field">
          <label for="np-address">项目地址</label>
          <input class="input" id="np-address" maxlength="200" placeholder="例如：上海市徐汇区…（可选）">
        </div>
        <div class="field error hidden" id="np-error"></div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (c) => c() },
      {
        label: '创建项目',
        variant: 'btn-primary',
        onClick: async (c, modal) => {
          const name = modal.querySelector('#np-name').value.trim();
          const address = modal.querySelector('#np-address').value.trim();
          const errorEl = modal.querySelector('#np-error');
          if (!name) {
            errorEl.textContent = '请输入项目名称';
            errorEl.classList.remove('hidden');
            return;
          }
          try {
            await api.createProject(name, address);
            c();
            toast('ok', '项目已创建', name);
            await onCreated();
          } catch (error) {
            errorEl.textContent = error?.message || '创建失败';
            errorEl.classList.remove('hidden');
          }
        },
      },
    ],
  });
}
