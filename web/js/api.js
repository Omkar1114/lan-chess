// HTTP and WebSocket access to the server.

/** Call the JSON API. Throws an Error carrying the server's message on failure. */
export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    let message = data?.detail ?? `Request failed (${response.status})`;
    if (Array.isArray(message)) {
      // FastAPI validation errors: [{loc: [...], msg: "..."}]
      message = message.map((e) => `${e.loc?.at(-1) ?? "input"}: ${e.msg}`).join("; ");
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

/**
 * WebSocket that reconnects automatically with exponential back-off, so a
 * refresh, a sleeping phone or a Wi-Fi hiccup simply resumes the stream.
 */
export class Socket {
  constructor(path, { onMessage, onStatus } = {}) {
    this.url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
    this.onMessage = onMessage ?? (() => {});
    this.onStatus = onStatus ?? (() => {});
    this.closed = false;
    this.delay = 500;
    this.queue = [];
    this.connect();
    this.keepAlive = setInterval(() => this.send({ type: "ping" }, false), 25000);
  }

  connect() {
    if (this.closed) return;
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.delay = 500;
      this.onStatus(true);
      for (const message of this.queue.splice(0)) this.ws.send(message);
    };
    this.ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type !== "pong") this.onMessage(message);
    };
    this.ws.onclose = () => {
      if (this.closed) return;
      this.onStatus(false);
      setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, 5000);
    };
  }

  /** Send now, or (if `queue`) as soon as the connection is back. */
  send(message, queue = true) {
    const text = JSON.stringify(message);
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(text);
    else if (queue) this.queue.push(text);
  }

  close() {
    this.closed = true;
    clearInterval(this.keepAlive);
    this.ws?.close();
  }
}
