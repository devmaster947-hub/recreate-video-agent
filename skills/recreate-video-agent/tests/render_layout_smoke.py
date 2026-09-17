"""Local renderer smoke test using synthetic anchor times, not a Gemini analysis."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import storyboard

video=Path(sys.argv[1])
root=Path(sys.argv[2]);root.mkdir(parents=True,exist_ok=True)
for count in (9,16):
    anchors=[{"timestamp":i*12/count} for i in range(count)]
    out=root/f'render-{count}.png'
    storyboard.render_board(storyboard.resolve_ffmpeg(None),video,anchors,out,240,464)
    print(json.dumps({"count":count,"file":str(out),"bytes":out.stat().st_size}))
