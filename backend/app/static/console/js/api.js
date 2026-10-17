/* 安龄智境 · API 客户端（JWT / 上传进度 / 二进制资源） */

const SESSION_KEY = 'anjing.console.session';

export class ApiError extends Error {
  constructor(message, status = 0, code = 'error') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export function getSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
export function setSession(session) {
  localStorage.setItem(SESSION_KEY, JSON.stringify({ ...session, savedAt: Date.now() }));
}
export function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}
export function getToken() {
  return getSession()?.token || '';
}
export function currentUser() {
  return getSession()?.user || null;
}
/* 角色只用于隐藏/禁用入口，服务端 require_admin 才是最终边界 */
export function isAdmin() {
  return currentUser()?.role === 'admin';
}

function onUnauthorized() {
  clearSession();
  window.dispatchEvent(new CustomEvent('anjing:unauthorized'));
}

async function readError(res) {
  let message = `请求失败（HTTP ${res.status}）`;
  try {
    const data = await res.json();
    if (data?.error?.message) message = data.error.message;
    else if (typeof data?.detail === 'string') message = data.detail;
  } catch { /* 保留默认信息 */ }
  return message;
}

export async function request(path, { method = 'GET', json, formData, signal } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body;
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  } else if (formData) {
    body = formData;
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body, signal });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new ApiError('网络异常，无法连接后端服务', 0, 'network');
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError('登录已过期，请重新登录', 401, 'unauthorized');
  }
  if (!res.ok) throw new ApiError(await readError(res), res.status);
  if (res.status === 204) return null;
  const type = res.headers.get('content-type') || '';
  return type.includes('application/json') ? res.json() : res;
}

export function uploadWithProgress(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path, true);
    const token = getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText || '{}')); }
        catch { resolve({}); }
        return;
      }
      if (xhr.status === 401) onUnauthorized();
      let message = `上传失败（HTTP ${xhr.status}）`;
      try {
        const data = JSON.parse(xhr.responseText || '{}');
        if (data?.error?.message) message = data.error.message;
      } catch { /* ignore */ }
      reject(new ApiError(message, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('上传中断，请检查网络后重试', 0, 'network'));
    const form = new FormData();
    form.append('files', file, file.name);
    xhr.send(form);
  });
}

/* —— 二进制资源（图片 / PDF）：带 Bearer 拉取后转 objectURL —— */
const assetCache = new Map();

export async function fetchAsset(path) {
  if (assetCache.has(path)) return assetCache.get(path);
  const promise = (async () => {
    const token = getToken();
    const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (res.status === 401) { onUnauthorized(); throw new ApiError('登录已过期', 401); }
    if (!res.ok) throw new ApiError(await readError(res), res.status);
    return URL.createObjectURL(await res.blob());
  })();
  assetCache.set(path, promise);
  try {
    return await promise;
  } catch (error) {
    assetCache.delete(path);
    throw error;
  }
}

export function releaseAssets() {
  assetCache.forEach(async (promise) => {
    try { URL.revokeObjectURL(await promise); } catch { /* ignore */ }
  });
  assetCache.clear();
}

export async function downloadAsset(path, filename) {
  const url = await fetchAsset(path);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/* —— 业务接口 —— */
export const api = {
  login: (email, password) => request('/api/auth/login', { method: 'POST', json: { email, password } }),
  me: () => request('/api/auth/me'),
  changePassword: (currentPassword, newPassword) => request('/api/auth/password', {
    method: 'POST',
    json: { current_password: currentPassword, new_password: newPassword },
  }),

  listUsers: () => request('/api/users'),
  createUser: (name, email, password) => request('/api/users', {
    method: 'POST',
    json: { name, email, password },
  }),
  updateUser: (id, payload) => request(`/api/users/${id}`, { method: 'PATCH', json: payload }),
  resetUserPassword: (id, password) => request(`/api/users/${id}/password`, {
    method: 'POST',
    json: { password },
  }),

  listProjects: (limit = 60) => request(`/api/projects?limit=${limit}`),
  createProject: (name, address) => request('/api/projects', { method: 'POST', json: { name, address } }),
  getProject: (id) => request(`/api/projects/${id}`),
  listScans: (projectId, limit = 60) => request(`/api/projects/${projectId}/scans?limit=${limit}`),
  createScan: (projectId, { deferProcessing = false } = {}) => request(`/api/projects/${projectId}/scans`, { method: 'POST', json: { capture_type: 'video', defer_processing: deferProcessing } }),
  importHouseDemo: (projectId) => request(`/api/projects/${projectId}/scans/house-demo`, { method: 'POST' }),
  getHouseRooms: (projectId) => request(`/api/projects/${projectId}/scans/house/rooms`),
  assembleHouse: (projectId, payload) => request(`/api/projects/${projectId}/scans/house`, { method: 'POST', json: payload }),
  getScan: (id) => request(`/api/scans/${id}`),
  uploadVideo: (scanId, file, onProgress, { deferProcessing = false } = {}) => uploadWithProgress(`/api/scans/${scanId}/upload${deferProcessing ? '?defer_processing=true' : ''}`, file, onProgress),
  startScan: (scanId) => request(`/api/scans/${scanId}/start`, { method: 'POST' }),
  putReferences: (scanId, measurements) => request(`/api/scans/${scanId}/references`, { method: 'PUT', json: { measurements } }),

  getReport: (scanId) => request(`/api/reports/scans/${scanId}`),
  getManifest: (scanId) => request(`/api/preview/${scanId}/manifest.json`),
  getHouseConnections: (scanId) => request(`/api/preview/${scanId}/house-connections.json`),
  getLayout: (scanId) => request(`/api/preview/${scanId}/layout.json`),
  getStructure: (scanId) => request(`/api/preview/${scanId}/structure.json`),
  getMeasurements: (scanId) => request(`/api/preview/${scanId}/measurements.json`),
  getSpatialMetrics: (scanId) => request(`/api/preview/${scanId}/spatial-metrics.json`),
  getRiskAssessment: (scanId) => request(`/api/preview/${scanId}/risk-assessment.json`),

  getSuggestions: (scanId) => request(`/api/assistant/scans/${scanId}/suggestions`),
  generateRoomProposals: (scanId, goal = '', { signal } = {}) => request(`/api/assistant/scans/${scanId}/proposals`, { method: 'POST', json: { goal: goal || null }, signal }),
  simulate: (scanId, payload) => request(`/api/assistant/scans/${scanId}/simulate`, { method: 'POST', json: payload }),
  solve: (scanId, metricCodes) => request(`/api/assistant/scans/${scanId}/solve`, { method: 'POST', json: { metric_codes: metricCodes } }),
  listSchemes: (scanId) => request(`/api/assistant/scans/${scanId}/schemes`),
  getScheme: (scanId, schemeId) => request(`/api/assistant/scans/${scanId}/schemes/${schemeId}`),
  createScheme: (scanId, payload, { signal } = {}) => request(`/api/assistant/scans/${scanId}/schemes`, { method: 'POST', json: payload, signal }),
  deleteScheme: (scanId, schemeId) => request(`/api/assistant/scans/${scanId}/schemes/${schemeId}`, { method: 'DELETE' }),
  exportSchemePdf: async (scanId, schemeId, pngBlob) => {
    const form = new FormData();
    form.append('plan', pngBlob, 'plan.png');
    const res = await request(`/api/assistant/scans/${scanId}/schemes/${schemeId}/export.pdf`, { method: 'POST', formData: form });
    return res.blob();
  },

  planAsset: (scanId, kind) => fetchAsset(`/api/preview/${scanId}/${kind === 'passage' ? 'passage_plan.png' : 'structure_plan.png'}`),
  reportPdf: (scanId) => fetchAsset(`/static/${scanId}/pdf`),
};
