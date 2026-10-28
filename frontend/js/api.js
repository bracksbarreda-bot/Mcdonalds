import { API_URL } from "./config.js";

const TOKEN_KEY = "dh_token";
const USER_KEY = "dh_user";

// El token se guarda en sessionStorage: se borra al cerrar la pestaña/navegador.
export const auth = {
  get token() { return sessionStorage.getItem(TOKEN_KEY); },
  set(token, user) {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  user() {
    try { return JSON.parse(sessionStorage.getItem(USER_KEY)); } catch { return null; }
  },
  clear() {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
  },
};

export class ApiError extends Error {
  constructor(message, status, data = {}) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/**
 * Llama a la API.
 *  api("/incidents", { query: {...} })
 *  api("/incidents", { method: "POST", json: {...} })
 *  api("/import/preview", { method: "POST", form: formData })
 *  api("/incidents/export.csv", { raw: true })  -> devuelve el Response
 */
export async function api(path, { method = "GET", json, form, query, raw = false } = {}) {
  const url = new URL(API_URL + path);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  const headers = {};
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form;
  }

  let res;
  try {
    res = await fetch(url, { method, headers, body });
  } catch {
    throw new ApiError("No se puede conectar con el servidor. ¿Está arrancado el backend (app.py)?", 0);
  }

  if (res.status === 401 && path !== "/auth/login") {
    auth.clear();
    location.hash = "#/login";
    throw new ApiError("Tu sesión ha caducado. Vuelve a entrar.", 401);
  }
  if (raw) {
    if (!res.ok) throw new ApiError(`Error ${res.status}`, res.status);
    return res;
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 403 && data.code === "must_change_password") {
    const u = auth.user();
    if (u) auth.set(auth.token, { ...u, must_change_password: true });
    location.hash = "#/cuenta";
  }
  if (!res.ok) throw new ApiError(data.error || `Error ${res.status}`, res.status, data);
  return data;
}
