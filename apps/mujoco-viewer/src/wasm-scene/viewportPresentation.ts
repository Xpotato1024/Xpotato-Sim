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
  if (view==="assist-top") {
    // operator frameへ合わせ、screen-right=-Y / screen-up=+X とする。
    return {target:[...center],position:[center[0],center[1],center[2]+distance],up:[1,0,0]};
  }
  // operatorの背後(-X)側から見て、screen-right=-Y / screen-up=+Z とする。
  return {target:[...center],position:[center[0]-distance,center[1],center[2]],up:[0,0,1]};
}
