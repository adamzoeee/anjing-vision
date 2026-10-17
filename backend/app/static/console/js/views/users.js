/* 用户管理（机构管理员）：建号 / 改名 / 重置密码 / 停用启用 + 本人改密。
 * 每个机构只允许一个管理员，因此管理员账号不能在这里被停用或重置。 */

import { api, currentUser, clearSession } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import { esc, toast, openModal, timeAgo, emptyState } from '../ui.js';

export async function usersView(ctx) {
  ctx.title([{ label: '用户管理', current: true }]);
  ctx.actions.innerHTML = '';

  const newBtn = document.createElement('button');
  newBtn.className = 'btn btn-primary';
  newBtn.innerHTML = `${icon('plus', 16)} 新建成员`;
  newBtn.addEventListener('click', () => openCreateUser(load));
  ctx.actions.appendChild(newBtn);

  const self = currentUser() || {};

  ctx.host.innerHTML = `
    <div class="view-users">
      <div class="glass panel" style="margin-bottom:14px">
        <div class="panel-head" style="justify-content:center">
          <div class="panel-title">${icon('shield', 16)} 权限说明</div>
        </div>
        <div style="display:flex;gap:12px;align-items:center">
          <div class="faint" style="flex:1;min-width:0;font-size:12.5px;line-height:1.6">
            机构管理员可管理项目、上传视频、拼接整屋与保存改造方案；成员为只读账号，可查看项目、点云、图纸、报告与沙盒模拟结果。
            数据按机构隔离，成员只能看到本机构内容。
          </div>
          <button class="btn" id="btn-self-password" style="flex:none">${icon('shield', 15)} 修改我的密码</button>
        </div>
      </div>
      <div class="glass panel" id="users-panel">
        <div class="skeleton-line shimmer" style="width:40%"></div>
        <div class="skeleton-block shimmer" style="height:180px;margin-top:12px"></div>
      </div>
    </div>`;

  ctx.host.querySelector('#btn-self-password').addEventListener('click', openSelfPassword);

  await load();

  async function load() {
    const panel = ctx.host.querySelector('#users-panel');
    try {
      const users = await api.listUsers();
      render(panel, users, self.id);
    } catch (error) {
      panel.innerHTML = emptyState({
        iconName: 'alert',
        title: '成员列表加载失败',
        desc: error?.message || '请稍后重试',
        action: '<button class="btn" id="btn-users-retry">重新加载</button>',
      });
      panel.querySelector('#btn-users-retry')?.addEventListener('click', load);
    }
  }

  function render(panel, users, selfId) {
    panel.innerHTML = `
      <div class="panel-head">
        <div class="panel-title">机构成员</div>
        <div class="faint" style="font-size:12.5px">共 ${users.length} 个账号</div>
      </div>
      <div class="user-rows">
        ${users.map((user) => userRow(user, selfId)).join('')}
      </div>`;
    panel.querySelectorAll('[data-user-action]').forEach((btn) => {
      const userId = Number(btn.dataset.id);
      const user = users.find((item) => item.id === userId);
      btn.addEventListener('click', async () => {
        const action = btn.dataset.userAction;
        if (action === 'reset') return openResetPassword(user, load);
        if (action === 'rename') return openRename(user, load);
        if (action === 'toggle') return toggleActive(user, btn, load);
      });
    });
  }

  function userRow(user, selfId) {
    const isSelf = user.id === selfId;
    const isAdminRow = user.role === 'admin';
    const status = user.is_active
      ? '<span class="badge ok"><span class="dot"></span>启用中</span>'
      : '<span class="badge bad"><span class="dot"></span>已停用</span>';
    const roleBadge = isAdminRow
      ? '<span class="badge info">机构管理员</span>'
      : '<span class="badge">成员</span>';
    const actions = isSelf
      ? '<span class="faint" style="font-size:12.5px">当前登录账号</span>'
      : isAdminRow
        ? '<span class="faint" style="font-size:12.5px">管理员账号不可停用</span>'
        : `
          <button class="btn btn-sm" data-user-action="rename" data-id="${user.id}">${icon('pen', 13)} 改名</button>
          <button class="btn btn-sm" data-user-action="reset" data-id="${user.id}">${icon('shield', 13)} 重置密码</button>
          <button class="btn btn-sm ${user.is_active ? '' : 'btn-primary'}" data-user-action="toggle" data-id="${user.id}">
            ${user.is_active ? `${icon('x', 13)} 停用` : `${icon('checkCircle', 13)} 启用`}
          </button>`;
    return `
      <div class="user-row glass">
        <div class="ur-main">
          <div class="ur-name">${esc(user.name)}${isSelf ? ' <span class="faint">（我）</span>' : ''}</div>
          <div class="ur-email">${esc(user.email)}</div>
        </div>
        <div class="ur-badges">${roleBadge}${status}</div>
        <div class="ur-time faint">${user.created_at ? `创建于 ${esc(timeAgo(user.created_at) || user.created_at.slice(0, 10))}` : ''}</div>
        <div class="ur-actions">${actions}</div>
      </div>`;
  }
}

function openCreateUser(onCreated) {
  openModal({
    title: '新建成员',
    desc: '新账号属于当前机构，角色为成员（只读）。',
    body: `
      <div class="col gap-4">
        <div class="field">
          <label for="nu-name">姓名 *</label>
          <input class="input" id="nu-name" maxlength="80" placeholder="例如：李护工">
        </div>
        <div class="field">
          <label for="nu-email">登录邮箱 *</label>
          <input class="input" id="nu-email" type="email" maxlength="120" placeholder="name@example.com">
        </div>
        <div class="field">
          <label for="nu-password">初始密码 *</label>
          <input class="input" id="nu-password" type="password" autocomplete="new-password" maxlength="64" placeholder="至少 8 位，交给成员后请尽快修改">
        </div>
        <div class="field error hidden" id="nu-error"></div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (close) => close() },
      {
        label: '创建成员',
        variant: 'btn-primary',
        onClick: async (close, modal) => {
          const name = modal.querySelector('#nu-name').value.trim();
          const email = modal.querySelector('#nu-email').value.trim();
          const password = modal.querySelector('#nu-password').value;
          const errorEl = modal.querySelector('#nu-error');
          const fail = (message) => {
            errorEl.textContent = message;
            errorEl.classList.remove('hidden');
          };
          if (!name || !email || !password) return fail('姓名、邮箱和初始密码都需要填写');
          if (password.length < 8) return fail('初始密码至少 8 位');
          try {
            await api.createUser(name, email, password);
            close();
            toast('ok', '成员已创建', `${name} · ${email}`);
            await onCreated();
          } catch (error) {
            fail(error?.message || '创建失败');
          }
        },
      },
    ],
  });
}

function openRename(user, onDone) {
  openModal({
    title: '修改成员姓名',
    desc: user.email,
    body: `
      <div class="col gap-4">
        <div class="field">
          <label for="nr-name">姓名 *</label>
          <input class="input" id="nr-name" maxlength="80" value="${esc(user.name)}">
        </div>
        <div class="field error hidden" id="nr-error"></div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (close) => close() },
      {
        label: '保存',
        variant: 'btn-primary',
        onClick: async (close, modal) => {
          const name = modal.querySelector('#nr-name').value.trim();
          if (!name) return;
          try {
            await api.updateUser(user.id, { name });
            close();
            toast('ok', '已保存', name);
            await onDone();
          } catch (error) {
            const errorEl = modal.querySelector('#nr-error');
            errorEl.textContent = error?.message || '保存失败';
            errorEl.classList.remove('hidden');
          }
        },
      },
    ],
  });
}

function openResetPassword(user, onDone) {
  openModal({
    title: '重置成员密码',
    desc: `${user.name} · ${user.email}`,
    body: `
      <div class="col gap-4">
        <div class="field">
          <label for="rp-password">新密码 *</label>
          <input class="input" id="rp-password" type="password" autocomplete="new-password" maxlength="64" placeholder="至少 8 位">
        </div>
        <div class="faint" style="font-size:12.5px">重置后成员所有已登录设备都需重新登录。</div>
        <div class="field error hidden" id="rp-error"></div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (close) => close() },
      {
        label: '重置密码',
        variant: 'btn-primary',
        onClick: async (close, modal) => {
          const password = modal.querySelector('#rp-password').value;
          const errorEl = modal.querySelector('#rp-error');
          if (password.length < 8) {
            errorEl.textContent = '新密码至少 8 位';
            errorEl.classList.remove('hidden');
            return;
          }
          try {
            await api.resetUserPassword(user.id, password);
            close();
            toast('ok', '密码已重置', '请通过安全方式告知成员');
            await onDone();
          } catch (error) {
            errorEl.textContent = error?.message || '重置失败';
            errorEl.classList.remove('hidden');
          }
        },
      },
    ],
  });
}

async function toggleActive(user, btn, onDone) {
  const next = !user.is_active;
  btn.disabled = true;
  try {
    await api.updateUser(user.id, { is_active: next });
    toast('ok', next ? '成员已启用' : '成员已停用', user.name);
    await onDone();
  } catch (error) {
    toast('error', next ? '启用失败' : '停用失败', error?.message || '');
    btn.disabled = false;
  }
}

export function openSelfPassword() {
  openModal({
    title: '修改我的密码',
    desc: '需要先验证当前密码。',
    body: `
      <div class="col gap-4">
        <div class="field">
          <label for="sp-current">当前密码 *</label>
          <input class="input" id="sp-current" type="password" autocomplete="current-password" maxlength="64">
        </div>
        <div class="field">
          <label for="sp-new">新密码 *</label>
          <input class="input" id="sp-new" type="password" autocomplete="new-password" maxlength="64" placeholder="至少 8 位">
        </div>
        <div class="field error hidden" id="sp-error"></div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (close) => close() },
      {
        label: '保存新密码',
        variant: 'btn-primary',
        onClick: async (close, modal) => {
          const currentPassword = modal.querySelector('#sp-current').value;
          const newPassword = modal.querySelector('#sp-new').value;
          const errorEl = modal.querySelector('#sp-error');
          if (!currentPassword || newPassword.length < 8) {
            errorEl.textContent = '请填写当前密码，新密码至少 8 位';
            errorEl.classList.remove('hidden');
            return;
          }
          try {
            await api.changePassword(currentPassword, newPassword);
            close();
            clearSession();
            toast('ok', '密码已更新', '请使用新密码重新登录');
            location.hash = '#/login';
          } catch (error) {
            errorEl.textContent = error?.message || '修改失败';
            errorEl.classList.remove('hidden');
          }
        },
      },
    ],
  });
}
