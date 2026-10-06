import {createHash} from "node:crypto";
import {readFileSync,readdirSync,statSync,writeFileSync} from "node:fs";
import {resolve} from "node:path";
import type {Plugin} from "vite";

/** sourceとlockのbyteを固定buildに束縛する。Git cleanやHEADだけには依存しない。 */
export function buildIdentity(appRoot:string):Plugin {
  const files:string[]=[];
  const walk=(name:string)=>{
    const path=resolve(appRoot,name);
    if(statSync(path).isDirectory()) for(const child of readdirSync(path))walk(`${name}/${child}`);
    else files.push(name);
  };
  for(const name of ["src","tooling","index.html","package.json","package-lock.json","vite.config.ts"])walk(name);
  const hash=createHash("sha256");
  for(const name of files.sort())hash.update(name+"\0").update(readFileSync(resolve(appRoot,name))).update("\0");
  const source_sha256=hash.digest("hex");
  return {name:"workbench-build-identity",writeBundle(options,bundle){
    if(!options.dir)throw new Error("固定buildにはoutput directoryが必要です");
    const assets:Record<string,string>={};
    for(const name of Object.keys(bundle))assets[name]=createHash("sha256").update(readFileSync(resolve(options.dir,name))).digest("hex");
    writeFileSync(resolve(options.dir,"workbench-build.json"),JSON.stringify({schema_version:"workbench-build/v1",source_sha256,assets}));}};
}
