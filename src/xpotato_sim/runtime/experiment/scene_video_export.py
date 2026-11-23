"""保存済み受動Scene試行を物理再実行せずnative描画する、既存export入口の共通adapter。"""
from __future__ import annotations

from bisect import bisect_right
from hashlib import sha256
from itertools import chain
import json
from math import ceil, isfinite
from pathlib import Path
import shutil
import subprocess
import tempfile

import mujoco
import numpy as np
from PIL import Image

from xpotato_sim.runtime.composition.launch_profile import decode_launch_profile, repository_workspace
from xpotato_sim.runtime.experiment.video_bundle import CAMERAS, _read_referenced
from xpotato_sim.runtime.scene.objects import canonical


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _state_frames(trial, provider, model, source):
    """検証済みフル積分状態だけを返す。qpos-onlyでplastic stateを作り直さない。"""
    analysis = trial.analysis()
    terminal = _read_referenced(source, "terminal.json", analysis["terminal_sha256"])
    start = _read_referenced(source, "start.json", terminal["start_ref"]["sha256"])
    initial = _read_referenced(source, "initial-state.json", start["initial_state_ref"]["sha256"])
    tick_states = (
        (row["simulation_time_s"], row["qpos"], row["qvel"], row["signals"]["scene_state_checkpoint_v1"])
        for row in trial.iter_rows(kind="tick")
    )
    checkpoints = []
    for timestamp, qpos, qvel, checkpoint in chain(
        [(initial["time_s"], initial["qpos"], initial["qvel"], initial)], tick_states
    ):
        if (checkpoint["model_sha256"]!=provider.model_sha256 or
                checkpoint["mujoco_version"]!=mujoco.__version__ or
                checkpoint["state_spec"]!=int(mujoco.mjtState.mjSTATE_INTEGRATION)):
            raise ValueError("native checkpoint engine/model/spec mismatch")
        values=np.asarray(checkpoint["integration_state"],dtype=np.float64)
        if values.size!=mujoco.mj_stateSize(model,mujoco.mjtState.mjSTATE_INTEGRATION) or not np.isfinite(values).all():
            raise ValueError("recorded native state dimension/finite mismatch")
        checkpoints.append((float(timestamp), values, qpos, qvel))
    if len(checkpoints)<2:
        raise ValueError("at least two recorded Scene frames required")
    times=[x[0] for x in checkpoints]
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError("recorded simulation time must be strictly increasing")
    return checkpoints


def _native_camera(model,preset):
    if preset not in CAMERAS:
        raise ValueError("unsupported named camera")
    camera=mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model,camera)
    # Frame the participating geometry, not the large support plane.
    initial=mujoco.MjData(model)
    mujoco.mj_forward(model,initial)
    centers=[initial.geom_xpos[i] for i in range(model.ngeom)
             if model.geom_type[i] != mujoco.mjtGeom.mjGEOM_PLANE]
    if model.nflex:
        centers.extend(initial.flexvert_xpos)
    camera.lookat[:]=np.mean(np.asarray(centers),axis=0) if centers else model.stat.center
    camera.distance=max(float(model.stat.extent)*.9,0.45)
    camera.azimuth={"operator":110,"iso":135,"front":90,"side":0,"top":90}[preset]
    camera.elevation={"operator":-28,"iso":-30,"front":0,"side":0,"top":-90}[preset]
    return camera


def export_scene_only_video(args, *, trial):
    """既存のexport_trial_video入口から、非Robot Sceneに使うnative描画先を切り替える。"""
    source=Path(args.trial).resolve()
    target=Path(args.output).resolve()
    sidecar=target.with_suffix(".json")
    requested={".mp4":target}
    if args.gif:
        requested[".gif"]=target.with_suffix(".gif")
    if args.webm:
        requested[".webm"]=target.with_suffix(".webm")
    if (target.suffix.lower()!=".mp4" or
            any(p.exists() for p in (*requested.values(),sidecar)) or
            source==target or source in target.parents):
        raise ValueError("new MP4 and sidecar paths are required, outside the trial")
    if (type(args.width) is not int or type(args.height) is not int or
            not 16<=args.width<=1920 or not 16<=args.height<=1080 or
            args.width%2 or args.height%2):
        raise ValueError("even bounded dimensions required")
    if type(args.fps) is not int or not 1<=args.fps<=120:
        raise ValueError("bounded integer fps required")
    if not isfinite(args.speed) or not .1<=args.speed<=4:
        raise ValueError("playback speed must be between 0.1 and 4")
    ffmpeg=shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg required")
    analysis=trial.analysis()
    if analysis["condition"]["schema_version"]!="trial-condition/v2":
        raise ValueError("not a passive Scene trial")
    config=dict(analysis["origin_profile"]["configuration"])
    workspace=repository_workspace()
    config["workspace"]=str(workspace)
    profile=decode_launch_profile(canonical(config),source_path=workspace/"profiles"/"recorded-scene.json")
    if profile.scene_plan.manifest.digest!=analysis["condition"]["scene_digest"]:
        raise ValueError("recorded Environment differs from reloaded scene")
    instance=profile.build_model()
    provider=instance.provider
    model=provider.model
    if provider.model_sha256!=analysis["condition"]["model_sha256"]:
        raise ValueError("recorded model digest differs from current executable model")
    if analysis["condition"]["numerical_condition"]!=provider.numerical_condition():
        raise ValueError("recorded model numerical conditions differ from current runtime")
    frames = _state_frames(trial, provider, model, source)
    timestamps = [frame[0] for frame in frames]
    duration = (timestamps[-1]-timestamps[0])/args.speed
    count=ceil(duration*args.fps-1e-9)
    if not 2<=count<=3600:
        raise ValueError("bounded output frame count required")
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scene-export-",dir=target.parent) as temporary:
        directory=Path(temporary)
        model.vis.global_.offwidth=args.width
        model.vis.global_.offheight=args.height
        model.vis.quality.shadowsize=2048
        camera=_native_camera(model,args.camera)
        data=mujoco.MjData(model)
        used=[]
        with mujoco.Renderer(model,height=args.height,width=args.width) as renderer:
            for index in range(count):
                t = timestamps[0] + index*args.speed/args.fps
                position = max(0, bisect_right(timestamps, t+1e-10)-1)
                timestamp,values,qpos,qvel=frames[position]
                mujoco.mj_setState(model,data,values,mujoco.mjtState.mjSTATE_INTEGRATION)
                mujoco.mj_forward(model,data)
                if not np.allclose(data.qpos,qpos,atol=1e-9,rtol=0) or not np.allclose(data.qvel,qvel,atol=1e-8,rtol=0):
                    raise ValueError("native checkpoint could not reconstruct recorded pose")
                renderer.update_scene(data,camera=camera)
                pixels=renderer.render()
                Image.fromarray(pixels).save(directory/f"{index:05d}.png")
                used.append({"frame":index,"recorded_tick_index":position,"recorded_time_s":timestamp})
        src=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-n","-framerate",str(args.fps),
             "-i",str(directory/"%05d.png")]
        cmds={".mp4":["-an","-c:v","libx264","-pix_fmt","yuv420p","-crf","23","-movflags","+faststart"],
              ".gif":["-filter_complex","split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3","-loop","0"],
              ".webm":["-an","-c:v","libvpx-vp9","-crf","32","-b:v","0","-pix_fmt","yuv420p"]}
        produced={}
        for suffix,path in requested.items():
            subprocess.run(src+cmds[suffix]+[str(path)],check=True,timeout=180)
            subprocess.run([ffmpeg,"-v","error","-nostdin","-i",str(path),"-f","null","-"],check=True,timeout=60)
            produced[suffix]={"path":path.name,"bytes":path.stat().st_size,"sha256":_hash(path)}
        evidence={"schema_version":"scene-recorded-video/v1",
                  "trial_id":analysis["trial_id"],"source_commit":analysis["software_revision"],
                  "native_exporter_sha256":_hash(Path(__file__)),"camera_lookat":camera.lookat.tolist(),
                  "camera_distance":camera.distance,"renderer":"MuJoCo 4 native OpenGL",
                  "native_mujoco_version":mujoco.__version__,"model_sha256":provider.model_sha256,
                  "scene_digest":profile.scene_plan.manifest.digest,
                  "terminal_sha256":analysis["terminal_sha256"],
                  "condition_sha256":analysis["condition_sha256"],
                  "fps":args.fps,"speed":args.speed,"camera":args.camera,
                  "frame_count":count,"recorded_frames":len(frames),
                  "size_px":[args.width,args.height],
                  "frame_source":"validated full integration checkpoints including plastic",
                  "interpolation":"none; preceding recorded simulation timestamp",
                  "physics_stepped_during_export":False,"used_frames":used,"files":produced}
        sidecar.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"scene_video_export":str(target),"files":produced,"frames":count},ensure_ascii=False))
