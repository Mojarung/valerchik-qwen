import type { RefObject } from "react";
import { Headset, Plus } from "lucide-react";
import { AudienceToggle } from "@/components/AudienceToggle";
import { Mascot } from "@/components/Mascot";
import type { Audience } from "@/lib/audience";
import type { MascotMood } from "@/lib/types";
import { addresseeText, type Addressee } from "./addressee";

/** Статус-строка диалога: маскот (или оператор) и чем он занят, роль и «Новый диалог».
 *  Только в активной беседе — на стартовом экране её роль играет приветствие. */
export function ChatHeader({
  homeRef, mascotAway, mood, listening, closed, addressee, operatorName, operatorTyping,
  audience, onAudience, onRestart,
}: {
  /** Место маскота в шапке: отсюда выбегает и сюда возвращается ремонтник. */
  homeRef: RefObject<HTMLDivElement | null>;
  mascotAway: boolean;
  mood: MascotMood;
  listening: boolean;
  closed: boolean;
  /** Кому передано обращение; null — ведёт Валерчик. */
  addressee: Addressee | null;
  /** Оператор уже подключился и пишет сам; null — либо Валерчик, либо ещё в очереди. */
  operatorName: string | null;
  /** Оператор набирает ответ прямо сейчас. */
  operatorTyping: boolean;
  audience: Audience | null;
  onAudience: (audience: Audience | null) => void;
  onRestart: () => void;
}) {
  // Как только обращение ушло оператору, Валерчик реплики больше не ищет и
  // не отвечает — показывать его «thinking»/«слушает» тут же вводит в заблуждение:
  // выглядит так, будто бот всё ещё за штурвалом и вот-вот ответит сам.
  const withOperator = addressee !== null;

  return (
    <div className="flex items-center gap-3 py-3">
      {withOperator ? (
        <div className="flex size-[52px] shrink-0 items-center justify-center rounded-full bg-(--color-warn-soft)">
          <Headset size={22} className="text-(--color-warn-ink)" />
        </div>
      ) : (
        /* на время починки маскот «уходит» отсюда — вместо него по экрану бежит ремонтник */
        <div ref={homeRef} className="transition-opacity duration-150"
             style={{ opacity: mascotAway ? 0 : 1 }}>
          {/* Слушает — только когда не занята делом поважнее: пока идёт поиск
              ответа, полезнее показать именно это. */}
          <Mascot mood={listening && (mood === "idle" || mood === "happy") ? "listening" : mood}
                  size={52} />
        </div>
      )}
      {/* min-w-0: длинный адресат («Служба технической поддержки ЕАИСТ») на
          телефоне переносится, а не выталкивает кнопки шапки за край */}
      <div className="mr-auto min-w-0">
        <div className="text-sm font-bold">
          {!withOperator ? "Валерчик на связи"
            : operatorName ? `${operatorName} ${operatorTyping ? "печатает…" : "на связи"}`
            : "Ждём специалиста"}
        </div>
        {/* После передачи строка под именем — кому адресованы сообщения (п. 4 ТЗ):
            линия поддержки словами или тот, кто решает вопрос по регламенту */}
        <div className="text-xs leading-snug text-(--color-ink-soft)">
          {closed ? "диалог завершён"
            : addressee ? `Кому: ${addresseeText(addressee)}`
            : mood === "thinking" ? "ищет в базе знаний…"
            : listening ? "слушает вас…"
            : "отвечает мгновенно, 24/7"}
        </div>
      </div>
      {/* Роль видна и переключается по ходу разговора: «а если я заказчик?» —
          это тот же диалог, а не новый */}
      {!closed && <AudienceToggle compact value={audience} onChange={onAudience} />}
      {/* На широком экране «Новый диалог» есть в панели обращений (и в свёрнутой полоске) */}
      <button className="btn btn-ghost !min-h-0 !py-2 text-xs lg:hidden" onClick={onRestart}>
        <Plus size={14} /> <span className="hidden sm:inline">Новый диалог</span>
      </button>
    </div>
  );
}
