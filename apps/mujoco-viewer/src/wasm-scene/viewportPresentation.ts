export type ViewLayout = "single" | "assist";
export interface PaneRect {x:number; y:number; width:number; height:number}
export interface ScenePane extends PaneRect {id:"main"|"assist-top"|"assist-front"}
/** DOMの描画矩形をcanvas CSS座標へ変換。headerは呼出元で除外する。 */
export function relativePane(canvas: PaneRect, pane: PaneRect): PaneRect {
  return {x:pane.x-canvas.x,y:pane.y-canvas.y,width:Math.max(0,pane.width),height:Math.max(0,pane.height)};
}
export function scissorRect(pane: PaneRect, canvasHeight:number): PaneRect {
  return {...pane,y:canvasHeight-pane.y-pane.height};
}
export function assistPose(center:readonly number[], extent:number, view:"assist-top"|"assist-front") {
  const distance=Math.max(1,extent*3);
  return {target:[...center],position:center.map((v,i)=>v+(i===(view==="assist-top"?2:0)?distance:0)),
    up:view==="assist-top"?[-1,0,0]:[0,0,1]};
}
