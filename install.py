import sys
import os.path
import subprocess
from huggingface_hub import snapshot_download

# it goes to root comfy folder
sys.path.append('..')

COMFYUI_ROOT = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_PATH = os.path.join(COMFYUI_ROOT, "models", "checkpoints", "IDM_VTON")
# WEIGHTS_PATH = "/comfyui/models/checkpoints/IDM_VTON"
download = False

if not os.path.exists(WEIGHTS_PATH): 
    download = True
    print(f"--- create folder Not Exists")
    os.makedirs(WEIGHTS_PATH)

HF_REPO_ID = "yisol/IDM-VTON"


def build_pip_install_cmds(args):
    if "python_embeded" in sys.executable or "python_embedded" in sys.executable:
        return [sys.executable, '-s', '-m', 'pip', 'install'] + args
    else:
        return [sys.executable, '-m', 'pip', 'install'] + args

def ensure_package():
    cmds = build_pip_install_cmds(['-r', 'requirements.txt'])
    subprocess.run(cmds, cwd=CUSTOM_NODES_PATH)


if __name__ == "__main__":
    ensure_package()
    if download:
        print(f"---------------Starting snapsnot_download {WEIGHTS_PATH}")
        snapshot_download(repo_id=HF_REPO_ID, local_dir=WEIGHTS_PATH)
        print(f"---------------End snapsnot_download")
    else: 
        print(f"{WEIGHTS_PATH} is present - no download need")
