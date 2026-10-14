"""Run the normal CLI as the account created by live_novel_test.py."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.data_paths import resolve_data_root
from _local_test_credentials import load


def main():
    credentials = load(Path(resolve_data_root()) / "users/.cli-novel-test.dpapi")
    if sys.argv[1:] == ["--show-login"]:
        # Explicit local option for the owner to sign into the desktop app.
        print(json.dumps(credentials, ensure_ascii=False))
        return 0
    env = os.environ.copy()
    env["DSA_TEST_PASSWORD"] = credentials["password"]
    prefix = [sys.executable, str(ROOT / "control_main.py"), "--username", credentials["username"]]
    auth = subprocess.run(prefix + ["auth", "grant", "--password-env", "DSA_TEST_PASSWORD",
        "--name", "测试账号命令", "--expires-days", "1", "--allow-agent", "--allow-manage", "--allow-approve"],
        capture_output=True, encoding="utf-8", env=env, cwd=ROOT)
    result = json.loads(auth.stdout)
    if not result.get("ok"):
        print(auth.stdout)
        return auth.returncode
    grant = result["data"]
    env["DEEPSEEKASS_CONTROL_TOKEN"] = grant["token"]
    try:
        return subprocess.run(prefix + (sys.argv[1:] or ["status"]), env=env, cwd=ROOT).returncode
    finally:
        revoked = subprocess.run(prefix + ["auth", "revoke", grant["grant"]["grant_id"],
            "--password-env", "DSA_TEST_PASSWORD"], env=env, cwd=ROOT, capture_output=True)
        if revoked.returncode:
            print("临时令牌撤销失败，将在一天后到期。", file=sys.stderr)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
