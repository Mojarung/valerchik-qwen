import { parseWsEvent, type WsEvent } from "@/lib/wsEvents";

/** Сколько сообщений держим до открытия сокета: человек за это время столько не наберёт. */
const MAX_PENDING = 20;

/** Подпротокол гостевого сокета; парный CHAT_WS_PROTOCOL — в app/security.py. */
export const CHAT_WS_PROTOCOL = "valerchik.v1";

/** Подпротоколы гостевого чата: версия и токен владельца. Токен идёт здесь, а
 *  не в адресе: адрес оседает в логах прокси и в истории браузера. */
export function ownerProtocols(owner: string): string[] {
  return [CHAT_WS_PROTOCOL, `owner.${owner}`];
}

/** WebSocket с автопереподключением и экспоненциальной паузой.
 *
 *  Сообщения, отправленные до открытия сокета (или во время переподключения),
 *  не теряются, а ждут его в очереди. Иначе вопрос, набранный сразу после
 *  открытия чата, молча пропадал: сокет ещё в CONNECTING, `send` некуда, а
 *  интерфейс уже показывает «думает» — и так до бесконечности. */
export function connectWs(
  url: string,
  onEvent: (event: WsEvent) => void,
  onStatus?: (connected: boolean) => void,
  protocols?: string[],
): { send: (data: unknown) => void; close: () => void } {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  const pending: string[] = [];
  const subscriptions = new Set<string>();
  const seen = new Set<string>();

  const open = () => {
    if (closed) return;
    ws = new WebSocket(url, protocols);
    ws.onopen = () => {
      attempt = 0;
      onStatus?.(true);
      for (const room of subscriptions) ws?.send(JSON.stringify({type: "subscribe", room}));
      // очередь уходит в порядке набора; сокет мог закрыться прямо в цикле —
      // тогда остаток дождётся следующего открытия
      while (pending.length && ws?.readyState === WebSocket.OPEN) ws.send(pending.shift()!);
    };
    ws.onmessage = (e) => {
      let event: WsEvent | null = null;
      try {
        const raw = JSON.parse(e.data);
        if (typeof raw.event_id === "string") {
          if (seen.has(raw.event_id)) return;
          seen.add(raw.event_id);
          if (seen.size > 2000) seen.delete(seen.values().next().value!);
        }
        event = parseWsEvent(raw);
      } catch {
        /* мусорный кадр игнорируем */
      }
      if (event) onEvent(event);
    };
    ws.onclose = () => {
      onStatus?.(false);
      if (!closed) {
        attempt += 1;
        setTimeout(open, Math.min(1000 * 2 ** attempt, 15000));
      }
    };
    ws.onerror = () => ws?.close();
  };
  open();

  return {
    send: (data) => {
      const command = data as {type?: string; room?: string};
      if (command.type === "subscribe" && command.room) subscriptions.add(command.room);
      if (command.type === "unsubscribe" && command.room) subscriptions.delete(command.room);
      const frame = JSON.stringify(data);
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(frame);
        return;
      }
      if (closed) return;
      pending.push(frame);
      if (pending.length > MAX_PENDING) pending.shift();
    },
    close: () => {
      closed = true;
      pending.length = 0;
      ws?.close();
    },
  };
}
