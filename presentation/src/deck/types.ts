import type { FC, ReactNode } from "react";
import type { MascotState } from "../mascot/Valerchik";

/** f — кадр демо слайда (0…demo, в покое зажат на demo), g — глобальный кадр для «живых» деталей */
export interface SlideProps {
  f: number;
  g: number;
}

export interface SlideDef {
  id: string;
  title: string; // для счётчика на странице
  demo: number;
  Component: FC<SlideProps>;
  mascot: (f: number, g: number) => MascotState;
}

/** переход ВХОДА в слайд: p 0→1, prev — предыдущий слайд в финальном кадре, next — новый в нулевом */
export interface TransitionDef {
  frames: number;
  render: (p: number, prev: ReactNode, next: ReactNode) => ReactNode;
  mascot: (p: number, from: MascotState, to: MascotState) => MascotState;
}
