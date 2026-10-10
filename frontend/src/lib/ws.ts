import { getToken } from "./api";

export const backoffMs = (attempt: number) => Math.min(5000, 500 * 2 ** attempt);

export function wsUrl(path: string, loc: Location = location, token: string | null = getToken()): string {
  const scheme = loc.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${loc.host}${path}` + (token ? `?token=${encodeURIComponent(token)}` : "");
}

export interface SocketHandlers {
  onMessage: (ev: MessageEvent) => void;
  onOpen?: () => void;
  onStatus?: (connected: boolean) => void;
}

/** A WebSocket that reconnects with exponential backoff until stop() is called. */
export class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private stopped = true;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private path: string, private handlers: SocketHandlers) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  send(data: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(data);
  }

  private connect(): void {
    const ws = new WebSocket(wsUrl(this.path));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.handlers.onStatus?.(true);
      this.handlers.onOpen?.();
    };
    ws.onmessage = (ev) => this.handlers.onMessage(ev);
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.handlers.onStatus?.(false);
      if (this.stopped) return;
      this.timer = setTimeout(() => this.connect(), backoffMs(this.attempt++));
    };
  }
}
