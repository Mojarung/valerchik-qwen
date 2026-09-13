import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Один тост на страницу с одним таймером. Четыре копии `setTimeout(() =>
 * setToast(""))` в админке жили с разными задержками и без `clearTimeout`, и
 * второе уведомление гасилось таймером первого.
 */
export function useToast(defaultMs = 4000): { toast: string; show: (text: string, ms?: number) => void } {
  const [toast, setToast] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((text: string, ms = defaultMs) => {
    if (timer.current) clearTimeout(timer.current);
    setToast(text);
    timer.current = setTimeout(() => setToast(""), ms);
  }, [defaultMs]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return { toast, show };
}
