/** 表示済みgeometryの境界から画角だけを決める。FK・可動域・安全判定ではない。 */
export type VectorTuple = [number, number, number];
export interface DisplayBounds { min: VectorTuple; max: VectorTuple }
const dot = (a: readonly number[], b: readonly number[]) => a.reduce((sum,v,i)=>sum+v*b[i],0);
const cross = (a: readonly number[], b: readonly number[]): VectorTuple =>
  [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit = (v: readonly number[]): VectorTuple | null => {
  const length=Math.hypot(...v);
  return v.length===3 && v.every(Number.isFinite) && length>1e-9 ? v.map(x=>x/length) as VectorTuple : null;
};
export function boundsCenter(bounds: DisplayBounds): VectorTuple | null {
  if (bounds.min.length!==3 || bounds.max.length!==3 || ![...bounds.min,...bounds.max].every(Number.isFinite) ||
      bounds.min.some((value,i)=>value>bounds.max[i])) return null;
  return bounds.min.map((v,i)=>(v+bounds.max[i])/2) as VectorTuple;
}
export function perspectiveBoundsFit(bounds: DisplayBounds, offset: readonly number[], up: readonly number[],
  aspect: number, fovDegrees=45, padding=1.18) {
  const target=boundsCenter(bounds), backward=unit(offset);
  if (!target || !backward || !Number.isFinite(aspect) || aspect<=0 ||
      !Number.isFinite(fovDegrees) || fovDegrees<=0 || fovDegrees>=175 || !Number.isFinite(padding) || padding<1) return null;
  const right=unit(cross(up,backward));
  if (!right) return null;
  const vertical=cross(backward,right);
  const tanV=Math.tan(fovDegrees*Math.PI/360), tanH=tanV*aspect;
  let distance=0.05;
  for (let i=0;i<8;i++) {
    const relative=target.map((v,axis)=>((i&(1<<axis))?bounds.max[axis]:bounds.min[axis])-v);
    const depth=dot(relative,backward);
    distance=Math.max(distance,depth+padding*Math.abs(dot(relative,right))/tanH,
      depth+padding*Math.abs(dot(relative,vertical))/tanV,depth+0.02);
  }
  return {target,position:target.map((v,i)=>v+backward[i]*distance) as VectorTuple, distance};
}
/** 上面XYと正面YZの水平Y縮尺を共通にする。境界は明示fit時に凍結したものを使う。 */
export function orthographicHalfWidth(bounds: DisplayBounds, topAspect: number, frontAspect: number, padding=1.18): number | null {
  if (!boundsCenter(bounds) || ![topAspect,frontAspect,padding].every(Number.isFinite) ||
      topAspect<=0 || frontAspect<=0 || padding<1) return null;
  const half=bounds.min.map((v,i)=>(bounds.max[i]-v)/2);
  return Math.max(0.05,half[1],half[0]*topAspect,half[2]*frontAspect)*padding;
}
