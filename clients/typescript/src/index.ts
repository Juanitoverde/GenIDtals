/**
 * GenIDtals REST API client for Node.js 18+, Deno, Bun and browsers.
 *
 * Zero dependencies: uses the platform `fetch`. Images may be passed as
 * Uint8Array / ArrayBuffer / Blob, or as base64 strings.
 *
 * Do not call the API directly from end-user browsers with a real API key;
 * call it from your backend, which owns sessions and decides who may verify.
 */

export type ImageInput = Uint8Array | ArrayBuffer | Blob | string;

export interface QualityReport {
  score: number;
  metrics: Record<string, number>;
  issues: string[];
}

export type Reason =
  | "ok"
  | "not_enrolled"
  | "locked_out"
  | "low_quality"
  | "liveness_failed"
  | "no_match";

export interface AuthResult {
  authenticated: boolean;
  reason: Reason;
  message: string;
  /** Present only when the server runs with --expose-scores. */
  confidence: number | null;
  threshold: number | null;
  modality_scores: Record<string, number> | null;
  liveness: { live: boolean; checks: Record<string, number>; reasons: string[] } | null;
  quality: QualityReport | null;
  locked_until: number | null;
  remaining_attempts: number | null;
  elapsed_ms: number;
}

export interface EnrollResult {
  user_id: string;
  references: number;
  quality: number[];
}

export interface UserStatus {
  user_id: string;
  enrolled: boolean;
  locked: boolean;
  locked_until: number | null;
}

export class GenIDtalsError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(`${status} ${code}: ${message}`);
    this.name = "GenIDtalsError";
    this.status = status;
    this.code = code;
  }
}

export interface ClientOptions {
  baseUrl: string;
  apiKey: string;
  /** Request timeout in milliseconds (default 30000). */
  timeoutMs?: number;
  /** Allow plain http:// to non-local hosts. */
  allowInsecure?: boolean;
  fetch?: typeof fetch;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function encodeImage(image: ImageInput): Promise<string> {
  if (typeof image === "string") return image;
  if (image instanceof Uint8Array) return bytesToBase64(image);
  if (image instanceof ArrayBuffer) return bytesToBase64(new Uint8Array(image));
  return bytesToBase64(new Uint8Array(await image.arrayBuffer()));
}

export class GenIDtalsClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {
    const url = new URL(options.baseUrl);
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      throw new Error("baseUrl must be an http:// or https:// URL");
    }
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.protocol !== "https:" && !local && !options.allowInsecure) {
      throw new Error("Use https:// (or set allowInsecure for trusted networks)");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 30000;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(this.baseUrl + path, {
        method,
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new GenIDtalsError(response.status, payload.error ?? "http_error",
          payload.message ?? response.statusText);
      }
      return payload as T;
    } finally {
      clearTimeout(timer);
    }
  }

  private user(userId: string): string {
    return encodeURIComponent(userId);
  }

  health(): Promise<{ status: string; version: string }> {
    return this.request("GET", "/v1/health");
  }

  async enroll(userId: string, images: ImageInput[], overwrite = false): Promise<EnrollResult> {
    return this.request("POST", `/v1/users/${this.user(userId)}/enroll`, {
      images: await Promise.all(images.map(encodeImage)),
      overwrite,
    });
  }

  async verify(userId: string, image: ImageInput, frames: ImageInput[] = []): Promise<AuthResult> {
    return this.request("POST", `/v1/users/${this.user(userId)}/verify`, {
      image: await encodeImage(image),
      frames: await Promise.all(frames.map(encodeImage)),
    });
  }

  status(userId: string): Promise<UserStatus> {
    return this.request("GET", `/v1/users/${this.user(userId)}`);
  }

  deleteUser(userId: string): Promise<{ user_id: string; deleted: boolean }> {
    return this.request("DELETE", `/v1/users/${this.user(userId)}`);
  }

  unlock(userId: string): Promise<{ user_id: string; unlocked: boolean }> {
    return this.request("POST", `/v1/users/${this.user(userId)}/unlock`);
  }

  /** Returns a sealed, subject-bound template (base64) to store in your own database. */
  async createTemplate(subjectId: string, images: ImageInput[]): Promise<string> {
    const result = await this.request<{ template: string }>("POST", "/v1/templates", {
      subject_id: subjectId,
      images: await Promise.all(images.map(encodeImage)),
    });
    return result.template;
  }

  async verifyTemplate(subjectId: string, template: string, image: ImageInput,
    frames: ImageInput[] = []): Promise<AuthResult> {
    return this.request("POST", "/v1/templates/verify", {
      subject_id: subjectId,
      template,
      image: await encodeImage(image),
      frames: await Promise.all(frames.map(encodeImage)),
    });
  }

  async resealTemplate(subjectId: string, template: string): Promise<string> {
    const result = await this.request<{ template: string }>("POST", "/v1/templates/reseal", {
      subject_id: subjectId,
      template,
    });
    return result.template;
  }

  async quality(image: ImageInput): Promise<QualityReport> {
    return this.request("POST", "/v1/quality", { image: await encodeImage(image) });
  }
}
