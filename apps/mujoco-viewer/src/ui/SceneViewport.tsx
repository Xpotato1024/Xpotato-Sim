import type {RefObject} from "react";
import type {MujocoSceneRenderer} from "../wasm-scene/mujocoSceneRenderer.js";
import {relativePane,type ScenePane,type ViewLayout} from "../wasm-scene/viewportPresentation.js";
/** headerとsceneのDOM矩形を分離し、canvasは共有する。 */
export function scenePanes(canvas:HTMLCanvasElement):ScenePane[] {
  const bounds=canvas.getBoundingClientRect();
  return Array.from(canvas.parentElement!.querySelectorAll<HTMLElement>('[data-scene-pane]')).map(element=>({
    ...relativePane(bounds,element.getBoundingClientRect()),id:element.dataset.scenePane as ScenePane['id']}));
}
export function SceneViewport({canvas,interaction,renderer,layout,visible=true}:{
  canvas:RefObject<HTMLCanvasElement|null>;interaction:RefObject<HTMLDivElement|null>;renderer:RefObject<MujocoSceneRenderer|null>;layout:ViewLayout;visible?:boolean;
}) {
  return <section className="operation-views" data-layout={layout}>
    <canvas ref={canvas} style={{visibility:visible?'visible':'hidden'}} aria-label="共有MuJoCo scene" />
    <div className="operation-pane operation-pane--main"><header><strong>自由視点 · Perspective</strong><span>Orbit 回転 / 移動 / 拡大</span>
      <button type="button" title="自由視点を操作者基準へ戻す（入力方向は変わりません）" onClick={()=>renderer.current?.setCameraView('operator')}>操作視点</button><button type="button" title="カメラの向きを保ち、全ビューを対象に合わせる" onClick={()=>renderer.current?.setCameraView('fit')}>全体</button></header><div ref={interaction} data-scene-pane="main" className="main-interaction" tabIndex={0} aria-label="自由視点のカメラ操作" /></div>
    {layout==='assist'&&<div className="assist-column">
      <div className="operation-pane"><header>上面 · XY · +Z→-Z · 右+Y / 下+X</header><div data-scene-pane="assist-top" /></div>
      <div className="operation-pane"><header>正面 · YZ · +X→-X · 右+Y / 上+Z</header><div data-scene-pane="assist-front" /></div>
    </div>}
  </section>;
}
