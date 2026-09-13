import { useEffect, useRef } from "react";
import { useVoiceLevel } from "@/lib/store";
import type { MascotMood } from "@/lib/types";
import { useEyeTracking } from "./useEyeTracking";
import "./mascot.css";

/**
 * Крыска-маскот службы поддержки (в гарнитуре оператора).
 * Рисунок повторяет стикер-референс docs/mascot/1-neutral.png.
 *
 * Состояния: idle (парит, моргает, ЗРАЧКИ СЛЕДЯТ ЗА КУРСОРОМ), thinking (перебирает
 * карточки картотеки), speaking (говорит), sorry (уши поникли), alert (строгая),
 * happy (подпрыгивает с искрами), listening (приложила лапку к уху и слушает).
 */
export function Mascot({ mood = "idle", size = 72 }: { mood?: MascotMood; size?: number }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const pupilsRef = useRef<SVGGElement>(null);
  // следим за курсором, когда маскот «ничего не делает»: спокойное ожидание и радость
  useEyeTracking(rootRef, pupilsRef, mood === "idle" || mood === "happy");

  // Громкость приезжает 12–20 раз в секунду. Кладём её в CSS-переменную прямо в
  // подписке, без setState: перерисовывать полторы сотни узлов SVG ради одного
  // числа, которое читает только CSS, — верный способ уронить кадры.
  useEffect(() => {
    if (mood !== "listening") return;
    const write = (level: number) => {
      rootRef.current?.style.setProperty("--m-level", Math.min(1, level * 6).toFixed(3));
    };
    write(useVoiceLevel.getState().level);
    return useVoiceLevel.subscribe((state) => write(state.level));
  }, [mood]);

  // Амплитуда тряски в alert — в пикселях от размера маскота, чтобы иконка 52px и
  // большой маскот на стартовом экране вздрагивали одинаково по ощущению. Пиксели, а не
  // проценты: процентный translate зависит от раскладки, и Firefox из-за этого
  // отказывается уводить анимацию на композитор.
  const vars = { "--m-lift": `${(size * 0.03).toFixed(2)}px` } as React.CSSProperties;

  return (
    <div ref={rootRef} className={`mascot mascot--${mood}`}
         style={{ width: size, height: size, ...vars }} aria-hidden>
      {/* Обёртка держит слои и отыгрывает тряску в alert. Трансформ обычного div браузер
          отдаёт композитору, тогда как трансформ <g> заставляет его каждый кадр заново
          растеризовать всю векторную графику внутри. */}
      <div className="mascot__float">
      {/* Хвост — отдельным SVG-слоем под телом. Он качается непрерывно, и будь он внутри
          общего SVG, его перерисовка каждый кадр обнуляла бы растр всего маскота — в
          Firefox это и превращало ровную левитацию в дрожь (он не умеет разносить
          элементы одного SVG по слоям, в отличие от Chrome). */}
      <svg className="mascot__layer mascot__layer--tail" viewBox="0 0 120 120">
        <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77"
          fill="none" stroke="#fff" strokeWidth="8.6" strokeLinecap="round" />
        <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77"
          fill="none" stroke="#f0a6b3" strokeWidth="4.6" strokeLinecap="round" />
      </svg>
      <svg className="mascot__layer" viewBox="0 0 120 120">
        <defs>
          <linearGradient id="m-fur" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a2a8b6" />
            <stop offset="100%" stopColor="#868d9d" />
          </linearGradient>
          <linearGradient id="m-ear" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f9c8ce" />
            <stop offset="100%" stopColor="#f3aeb9" />
          </linearGradient>
          <linearGradient id="m-belly" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fbf6f3" />
            <stop offset="100%" stopColor="#f2ebe6" />
          </linearGradient>
        </defs>

        {/* фоновое кольцо-настроение */}
        <circle className="mascot__ring" cx="60" cy="62" r="50" />

        {/* тень «на полу»: статична и вне анимируемой группы. Раньше объём давал
            feDropShadow внутри .mascot__body — гауссово размытие пересчитывалось
            на каждом кадре покачивания и съедало бюджет кадра */}
        <ellipse cx="60" cy="115" rx="25" ry="3.6" fill="#8b7a80" opacity="0.16" />

        <g className="mascot__body">
          {/* белая стикерная обводка силуэта */}
          <g className="mascot__sticker">
            <circle cx="30" cy="31" r="19" fill="#fff" />
            <circle cx="90" cy="31" r="19" fill="#fff" />
            <path d="M60 20 C81 20 94 34 94 53 C94 62 91 69 88 74
                     C93 81 95 89 95 96 C95 108 81 114 60 114
                     C39 114 25 108 25 96 C25 89 27 81 32 74
                     C29 69 26 62 26 53 C26 34 39 20 60 20 Z" fill="#fff" />
          </g>

          {/* уши */}
          <g className="mascot__ear mascot__ear--left">
            <circle cx="30" cy="31" r="15" fill="url(#m-fur)" />
            <circle cx="30.5" cy="31.5" r="10.8" fill="url(#m-ear)" />
          </g>
          <g className="mascot__ear mascot__ear--right">
            <circle cx="90" cy="31" r="15" fill="url(#m-fur)" />
            <circle cx="89.5" cy="31.5" r="10.8" fill="url(#m-ear)" />
          </g>

          {/* голова + тело единым грушевидным силуэтом */}
          <path d="M60 24 C79 24 90 37 90 54 C90 62 87 68 84 73
                   C89 80 91 87 91 94 C91 105 79 110 60 110
                   C41 110 29 105 29 94 C29 87 31 80 36 73
                   C33 68 30 62 30 54 C30 37 41 24 60 24 Z" fill="url(#m-fur)" />

          <ellipse cx="60" cy="94" rx="17.5" ry="14.5" fill="url(#m-belly)" />

          {/* лапки (без атрибута transform: CSS transform-box: fill-box ломает его систему координат) */}
          <ellipse className="mascot__paw mascot__paw--left" cx="43" cy="92" rx="5.4" ry="7"
            fill="#aab0bd" stroke="#8f96a5" strokeWidth="0.8" />
          <ellipse className="mascot__paw mascot__paw--right" cx="77" cy="92" rx="5.4" ry="7"
            fill="#aab0bd" stroke="#8f96a5" strokeWidth="0.8" />

          {/* гарнитура оператора */}
          <path d="M31 47 C32 30 44 25 60 25 C76 25 88 30 89 47"
            fill="none" stroke="#2b303a" strokeWidth="3.6" strokeLinecap="round" />
          <circle cx="31" cy="49" r="6.2" fill="#2b303a" />
          <circle cx="89" cy="49" r="6.2" fill="#2b303a" />
          <path d="M89 55 C90 64 84 69 78 70" fill="none" stroke="#2b303a"
            strokeWidth="2" strokeLinecap="round" />
          <circle cx="77" cy="70" r="2.9" fill="#d92b3a" />

          {/* декоративные бровки как на стикере */}
          <path d="M44 42.5 Q49 39.5 54 42.5" fill="none" stroke="#7d8494"
            strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
          <path d="M76 42.5 Q71 39.5 66 42.5" fill="none" stroke="#7d8494"
            strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
          {/* брови настроения (sorry/alert) */}
          <path className="mascot__brow mascot__brow--left" d="M43 44 L53 46"
            fill="none" stroke="#3c424e" strokeWidth="2.1" strokeLinecap="round" />
          <path className="mascot__brow mascot__brow--right" d="M77 44 L67 46"
            fill="none" stroke="#3c424e" strokeWidth="2.1" strokeLinecap="round" />

          {/* щёчки */}
          <ellipse cx="39" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62" />
          <ellipse cx="81" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62" />

          {/* глаза: белок -> зрачки (следят за курсором) -> блики */}
          <g className="mascot__eyes">
            <ellipse cx="49" cy="56" rx="9.6" ry="10.6" fill="#ffffff" />
            <ellipse cx="71" cy="56" rx="9.6" ry="10.6" fill="#ffffff" />
            <g className="mascot__pupil-group" ref={pupilsRef}>
              <ellipse cx="49" cy="56.6" rx="6.9" ry="8" fill="#20242c" />
              <ellipse cx="71" cy="56.6" rx="6.9" ry="8" fill="#20242c" />
              <circle cx="46.7" cy="53.4" r="2.4" fill="#fff" />
              <circle cx="68.7" cy="53.4" r="2.4" fill="#fff" />
              <circle cx="51.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9" />
              <circle cx="73.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9" />
            </g>
          </g>

          {/* мордочка */}
          <ellipse cx="60" cy="69" rx="13.5" ry="10.5" fill="#faf5f2" />
          <ellipse cx="60" cy="64.5" rx="4.2" ry="3.2" fill="#ef94a4" />
          <path className="mascot__mouth" d="M55 70.5 Q60 75.5 65 70.5" fill="none"
            stroke="#3c424e" strokeWidth="1.9" strokeLinecap="round" />

          {/* усы */}
          <g stroke="#c6ccd6" strokeWidth="1.4" strokeLinecap="round">
            <path d="M47 64 L34 61.5" /><path d="M47 67.5 L33 68" /><path d="M47 71 L34.5 74" />
            <path d="M73 64 L86 61.5" /><path d="M73 67.5 L87 68" /><path d="M73 71 L85.5 74" />
          </g>
        </g>

        {/* Щётка (fixing): крыска вычищает латиницу из сообщения.
            Рисуем в локальных координатах «ручкой вниз, ворсом вверх» и поворачиваем
            целиком — так пропорции щётки задаются понятными числами, а не подгонкой
            диагональных отрезков. Габарит держим внутри viewBox 120×120: раньше ворс
            уезжал за x=120 и обрезался краем SVG, из-за чего кисть читалась как осколок. */}
        <g className="mascot__brush">
          <g transform="translate(82 111) rotate(39)">
            {/* деревянная ручка (белая подложка — контур поверх лапы) */}
            <rect x="-4.7" y="-27" width="9.4" height="29" rx="4.7" fill="#fff" />
            <rect x="-3.5" y="-26" width="7" height="27" rx="3.5" fill="#c98f4e" />
            <rect x="-2" y="-23.5" width="1.8" height="20" rx="0.9" fill="#e0a862"
              opacity="0.75" />
            {/* металлический обод с заклёпкой */}
            <rect x="-7.4" y="-35.4" width="14.8" height="10.8" rx="2.6" fill="#fff" />
            <rect x="-6.3" y="-34.4" width="12.6" height="8.8" rx="2" fill="#8f96a5" />
            <rect x="-6.3" y="-33.6" width="12.6" height="2.2" rx="1.1" fill="#c2c8d3"
              opacity="0.7" />
            <circle cx="0" cy="-29.8" r="1.1" fill="#6f7686" />
            {/* колодка ворса: книзу уже, к рабочему краю расходится веером */}
            <path d="M-6.3 -34 L6.3 -34 L8.4 -44.6 L-8.4 -44.6 Z" fill="#fff" stroke="#fff"
              strokeWidth="3" strokeLinejoin="round" />
            <path d="M-6.3 -34 L6.3 -34 L8.4 -44.6 L-8.4 -44.6 Z" fill="#79808f"
              stroke="#5d6472" strokeWidth="1.2" strokeLinejoin="round" />
            {/* отдельные пучки щетины + пара выбившихся ворсинок на рабочем крае */}
            <g stroke="#aeb5c2" strokeWidth="1.1" strokeLinecap="round" opacity="0.85">
              <path d="M-4.2 -34.6 L-5.9 -43.9" /><path d="M-1.4 -34.6 L-2 -44.2" />
              <path d="M1.4 -34.6 L2 -44.2" /><path d="M4.2 -34.6 L5.9 -43.9" />
            </g>
            <g stroke="#5d6472" strokeWidth="1" strokeLinecap="round" opacity="0.65">
              <path d="M-3.4 -44.6 L-3.8 -47" /><path d="M3.4 -44.6 L3.9 -46.8" />
            </g>
          </g>
        </g>

        {/* пыль из-под ворса: клубы разного размера разлетаются и тают */}
        <g className="mascot__dust" fill="#c4cad4">
          <circle cx="111" cy="73" r="2.8" /><circle cx="106" cy="66" r="2" />
          <circle cx="114" cy="80" r="2.4" /><circle cx="103" cy="61" r="1.6" />
          <circle cx="116" cy="70" r="1.8" />
        </g>

        {/* Звуковые волны (listening): расходятся от уха, к которому приложена лапка.
            Рисуем вне группы тела — иначе наклон корпуса таскал бы их за собой,
            а звук в комнате от позы крыски не зависит. */}
        <g className="mascot__waves" fill="none" stroke="var(--color-brand, #c2242c)"
           strokeWidth="3" strokeLinecap="round">
          <path className="mascot__wave" d="M104 26 A 8 8 0 0 1 104 42" />
          <path className="mascot__wave" d="M108 21 A 13.5 13.5 0 0 1 108 47" />
          <path className="mascot__wave" d="M112 16 A 19 19 0 0 1 112 52" />
        </g>

        {/* мысль (thinking) */}
        <g className="mascot__thought">
          <circle cx="101" cy="20" r="3.4" fill="#c9ccd4" />
          <circle cx="109" cy="12" r="5" fill="#dfe2e8" />
        </g>
        {/* картотека (thinking): крыска перебирает карточки базы знаний */}
        <g className="mascot__cardfile">
          <g className="mascot__cards">
            <rect className="mascot__carditem" x="7" y="84" width="8" height="13" rx="2"
              fill="#f4f5f7" stroke="#c9ccd4" strokeWidth="0.8" />
            <rect className="mascot__carditem" x="15" y="84" width="8" height="13" rx="2"
              fill="#eef0f3" stroke="#c9ccd4" strokeWidth="0.8" />
            <rect className="mascot__carditem" x="23" y="84" width="8" height="13" rx="2"
              fill="#f4f5f7" stroke="#c9ccd4" strokeWidth="0.8" />
            <rect className="mascot__carditem" x="31" y="84" width="8" height="13" rx="2"
              fill="#eef0f3" stroke="#c9ccd4" strokeWidth="0.8" />
          </g>
          <rect x="3" y="96" width="40" height="17" rx="4" fill="#5b626f" />
          <rect x="3" y="96" width="40" height="4" rx="2" fill="#6d7481" />
          <rect x="18" y="103" width="10" height="4" rx="2" fill="#9aa0ae" />
        </g>
        {/* искры (happy) */}
        <g className="mascot__sparks" stroke="#f5a623" strokeWidth="2" strokeLinecap="round">
          <path d="M12 42 l0 7 M8.5 45.5 l7 0" />
          <path d="M108 66 l0 7 M104.5 69.5 l7 0" />
          <path d="M17 104 l0 5 M14.5 106.5 l5 0" />
        </g>
      </svg>
      </div>
    </div>
  );
}
