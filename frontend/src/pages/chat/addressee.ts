/** Кому передано обращение. Числовой id направления остаётся внутренним. */
export interface Addressee {
  /** Наша линия, где обращение ждёт специалиста. */
  line: number;
  /** Её название из настроек: «Техническая поддержка»; пусто — сервер не прислал. */
  lineLabel: string;
  /** Кто решает вопрос по регламенту, если не наша линия: СКК, Арбитражная
   *  комиссия, СТП ЕАИСТ. Пусто — решает наша линия. */
  externalLabel: string;
}

const ourDirection = ({ lineLabel }: Addressee) => lineLabel || "Поддержка";

/** Для шапки: понятное название направления или внешний адресат. */
export function addresseeText(addressee: Addressee): string {
  return addressee.externalLabel || ourDirection(addressee);
}

/** Для плашки под шапкой: куда ушло обращение и кто подключится к этому чату.
 *  Внешнему адресату в чат не прийти — пишет всё равно наш специалист, и это
 *  нужно сказать, иначе непонятно, кого ждать. */
export function transferText(addressee: Addressee): string {
  return addressee.externalLabel
    ? `Этот вопрос рассматривает ${addressee.externalLabel}. В чате появится инструкция, как туда обратиться.`
    : `Обращение передано в направление «${ourDirection(addressee)}». Специалист подключится к этому чату.`;
}
