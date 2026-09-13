import { Composition } from "remotion";
import { H, RENDER_FPS, SUB, W } from "../anim";
import { Deck } from "../deck/Deck";
import { TOTAL } from "../deck/timeline";

/** та же колода для Remotion Studio и рендера в MP4: npm run studio / npm run render */
export function Root() {
  return <Composition id="Deck" component={Deck} durationInFrames={TOTAL * SUB} fps={RENDER_FPS} width={W} height={H} />;
}
