/* 项目管理工作台：KPI 概览 + 项目卡片网格 */

import { api, isAdmin } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import {
  esc, toast, countUp, openModal, confirmDialog, blueprintSVG, timeAgo, fmtScore, scoreTone,
  emptyState,
} from '../ui.js';
import { scoreRingHTML, mountRings } from '../charts.js';
import { navigate } from '../app.js';

export async function projectsView(ctx) {
  ctx.title([{ label: '项目工作台', current: true }]);
  ctx.actions.innerHTML = '';
  const admin = isAdmin();

  if (admin) {
    const newBtn = document.createElement('button');
    newBtn.className = 'btn btn-primary';
    newBtn.innerHTML = `${icon('plus', 16)} 新建项目`;
    newBtn.addEventListener('click', () => openCreateProject(load));
    ctx.actions.appendChild(newBtn);
  }

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
          ${admin ? `
          <div class="new-project-card" id="card-new-project" role="button" tabindex="0">
            <div class="np-inner">
              <div class="np-plus">${icon('plus', 20)}</div>
              <div>新建项目</div>
              <div style="font-size:12px;color:var(--text-4)">为一处住宅建立评估项目</div>
            </div>
          </div>` : ''}
        </div>` : `
        <div class="glass panel">
          ${emptyState({
            iconName: 'folder',
            title: '还没有项目',
            desc: admin
              ? '创建第一个项目，上传房间视频，系统将自动完成三维重建与风险评估。'
              : '当前机构还没有项目。成员账号为只读，请让机构管理员创建项目。',
            action: admin ? '<button class="btn btn-primary" id="btn-empty-new">新建项目</button>' : '',
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
    });
    ctx.host.querySelectorAll('[data-project-action]').forEach((button) => {
      button.addEventListener('click', async (event) => {
        event.stopPropagation();
        const project = projects.find((item) => item.id === Number(button.dataset.id));
        if (!project) return;
        if (button.dataset.projectAction === 'rename') openRenameProject(project);
        if (button.dataset.projectAction === 'delete') await removeProject(project, button);
      });
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
      <article class="project-card glass rise" style="--i:${index + 4}" data-id="${project.id}">
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
          ${admin ? `
          <div class="pc-manage">
            <button class="btn btn-sm" type="button" data-project-action="rename" data-id="${project.id}" aria-label="重命名项目 ${esc(project.name)}">${icon('pen', 14)} 重命名</button>
            <button class="btn btn-sm btn-danger" type="button" data-project-action="delete" data-id="${project.id}" aria-label="删除项目 ${esc(project.name)}">${icon('trash', 14)} 删除</button>
          </div>` : ''}
          <div class="pc-foot">
            ${icon('clock', 13)} ${lastUpdate ? `最近更新 ${esc(lastUpdate)}` : '尚未创建房间'}
            <span class="grow"></span>
            <button class="pc-open" type="button" aria-label="进入项目 ${esc(project.name)}">进入工作台 ${icon('arrowRight', 14)}</button>
          </div>
        </div>
      </article>`;
  }

  function openRenameProject(project) {
    openModal({
      title: '重命名项目',
      desc: `项目编号 P${String(project.id).padStart(3, '0')}`,
      body: `
        <div class="field">
          <label for="rename-project-name">项目名称</label>
          <input class="input" id="rename-project-name" maxlength="120" value="${esc(project.name)}">
        </div>
        <div class="field error hidden" id="rename-project-error" role="alert"></div>`,
      actions: [
        { label: '取消', variant: 'btn-ghost', onClick: (close) => close() },
        {
          label: '保存名称',
          variant: 'btn-primary',
          onClick: async (close, modal) => {
            const input = modal.querySelector('#rename-project-name');
            const errorEl = modal.querySelector('#rename-project-error');
            const name = input.value.trim();
            if (!name) {
              errorEl.textContent = '请输入项目名称';
              errorEl.classList.remove('hidden');
              input.focus();
              return;
            }
            const button = modal.querySelector('.modal-actions .btn-primary');
            button.disabled = true;
            try {
              await api.renameProject(project.id, name);
              close();
              toast('ok', '项目已重命名', name);
              await load();
            } catch (error) {
              errorEl.textContent = error?.message || '重命名失败';
              errorEl.classList.remove('hidden');
              button.disabled = false;
            }
          },
        },
      ],
    });
  }

  async function removeProject(project, button) {
    const confirmed = await confirmDialog({
      title: `删除项目：${project.name}`,
      message: '这会永久删除项目及其全部 Scan、视频、点云、报告、改造方案和中间产物，且不可恢复。',
      confirmText: '确认删除',
      danger: true,
    });
    if (!confirmed) return;
    button.disabled = true;
    try {
      await api.deleteProject(project.id);
      toast('ok', '项目已删除', project.name);
      await load();
    } catch (error) {
      toast('error', '删除失败', error?.message || '');
      button.disabled = false;
    }
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
