/**
 * api.js — thin fetch wrapper for the CA Office Desk frontend.
 * All requests go to /api/* which Vite proxies to Flask in dev
 * and which Flask serves directly in production.
 */

const BASE = import.meta.env.VITE_API_BASE_URL || '';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  get:   (path)        => request('GET',   path),
  patch: (path, body)  => request('PATCH', path, body),
  post:  (path, body)  => request('POST',  path, body),
};

// Convenience domain helpers
export const getMe           = ()         => api.get('/api/me');
export const getTodayTasks   = ()         => api.get('/api/dashboard/today');
export const getClients      = ()         => api.get('/api/clients');
export const getGSTStatus    = ()         => api.get('/api/gst/status');
export const getDocuments    = ()         => api.get('/api/documents/pending');
export const getApprovals    = ()         => api.get('/api/approvals');
export const getReports      = ()         => api.get('/api/reports/summary');

export const patchTaskStatus = (id, status, remarks = '') =>
  api.patch(`/api/tasks/${id}/status`, { status, remarks });

export const postTaskEmail     = (id, payload) => api.post(`/api/tasks/${id}/email`, payload);
export const postTaskWhatsApp  = (id, payload) => api.post(`/api/tasks/${id}/whatsapp`, payload);
export const postApprove       = (id)          => api.post(`/api/approvals/${id}/approve`, {});
export const postRequestChanges= (id, remarks) => api.post(`/api/approvals/${id}/request-changes`, { remarks });
export const postReject        = (id, remarks) => api.post(`/api/approvals/${id}/reject`, { remarks });
