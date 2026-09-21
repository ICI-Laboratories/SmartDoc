export type Document = {
  id: string;
  name: string;
  size: number;
  created_at: string;
  status: "queued" | "processing" | "ready" | "failed";
  page_count: number | null;
  error: string | null;
  embedding_error: string | null;
  summary: string | null;
  category: string | null;
  summary_error: string | null;
  summary_requested: boolean;
};
export type Hit = {
  id: string;
  name: string;
  page: number;
  content: string;
  score: number;
};
export type Source = { id: string; name: string; page: number; text: string };
export type Catalog = {
  items: Document[];
  next: { before: string; before_id: string } | null;
  stats: { total: number; pending: number; bytes: number };
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export function csrf() {
  return decodeURIComponent(
    document.cookie
      .split("; ")
      .find((c) => c.startsWith("smartdoc_csrf="))
      ?.split("=")
      .slice(1)
      .join("=") ?? "",
  );
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrf());
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      typeof payload.detail === "string"
        ? payload.detail
        : payload.error || "No se pudo completar la solicitud.",
    );
  }
  return response.json();
}
export function uploadFile(
  file: File,
  progress: (n: number) => void,
): Promise<{ document: Document; duplicate: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.setRequestHeader("X-CSRF-Token", csrf());
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress(Math.round((100 * e.loaded) / e.total));
    };
    xhr.onerror = () =>
      reject(
        new Error(
          "Se interrumpió la conexión. Puedes volver a subir el archivo.",
        ),
      );
    xhr.onload = () => {
      try {
        const result = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(result);
        else
          reject(
            new ApiError(
              xhr.status,
              result.detail || result.error || "No se pudo subir el archivo.",
            ),
          );
      } catch {
        reject(new Error("El servidor no devolvió una respuesta válida."));
      }
    };
    const data = new FormData();
    data.append("file", file);
    xhr.send(data);
  });
}
export const states = {
  queued: "En cola",
  processing: "Procesando",
  ready: "Listo",
  failed: "Necesita revisión",
};
export function bytes(value: number) {
  return value < 1048576
    ? `${Math.round(value / 1024)} KB`
    : `${(value / 1048576).toFixed(1)} MB`;
}
