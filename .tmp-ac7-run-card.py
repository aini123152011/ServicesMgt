"""AC7：把 usage 卡片里的命令逐字跑一遍（只替换 {{host}}/{{port}} 占位符）。

为什么用程序读 manifest 而不是手抄：手抄会把「照抄能不能跑」这个判据本身弄丢——
卡片是给测试人员复制的，验的必须是那串字符。BMC 目标条目跳过（要真机，且需单独授权）。
"""

import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HOST = sys.argv[1]
PORT = sys.argv[2]
MANIFEST = Path(sys.argv[3])

usage = yaml.safe_load(io.open(MANIFEST, encoding="utf-8"))["usage"]
failures: list[str] = []

for index, entry in enumerate(usage):
    target = entry.get("target", "")
    if target == "BMC":
        print(f"[{index}] target=BMC —— 跳过（需要真机 BMC，且 AC4/AC5 待用户授权）")
        continue
    command = entry["command"].strip()
    rendered = command.replace("{{host}}", HOST).replace("{{port}}", PORT)
    workdir = Path(tempfile.mkdtemp(prefix="ac7-"))
    print(f"[{index}] target={target} —— 逐字执行，工作目录 {workdir}")
    print("  " + rendered.replace("\n", "\n  "))
    try:
        proc = subprocess.run(  # noqa: S602 - 就是要按卡片原文跑 shell
            ["bash", "-c", rendered],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        failures.append(f"entry[{index}] 超时")
        print("  ✗ 超时")
        shutil.rmtree(workdir, ignore_errors=True)
        continue
    out = (proc.stdout + proc.stderr).strip()
    print(f"  exit={proc.returncode}")
    for line in out.splitlines()[:8]:
        print("    " + line)
    if proc.returncode != 0:
        failures.append(f"entry[{index}] exit={proc.returncode}")
    else:
        verify = re.search(r"^\S+: OK$", proc.stdout, re.M)
        if verify:
            print(f"  ✓ {verify.group(0)}")
    shutil.rmtree(workdir, ignore_errors=True)

print()
print("结论：" + ("全部通过" if not failures else "失败 " + "；".join(failures)))
sys.exit(1 if failures else 0)
