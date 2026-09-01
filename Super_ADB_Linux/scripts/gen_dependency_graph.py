# -*- coding: utf-8 -*-
"""
生成 Super_ADB 模块依赖图（Graphviz dot / svg / png）
=====================================================
静态 AST 解析 Super_ADB_Win 下所有 .py 的 import / from ... import
（含函数内的延迟导入），只保留项目内模块之间的依赖边，按目录分 cluster，
调用系统 Graphviz `dot` 渲染为 svg / png。

用法：
    python gen_dependency_graph.py
前置：系统已安装 Graphviz 且 dot.exe 位于
    C:/Program Files/Graphviz/bin/dot.exe
（或已加入 PATH，脚本会优先用 which('dot') 回退）
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../Super_ADB_Win/脚本
ROOT = HERE.parent                               # .../Super_ADB_Win
BASE = HERE.parent.parent / "依赖关系图"          # 项目根/依赖关系图
DOT = BASE.with_suffix(".dot")
DOTEXE = r"C:/Program Files/Graphviz/bin/dot.exe"

FONT = "Microsoft YaHei"
LABEL = {
    "root": "Super_ADB_Win/ 核心常驻",
    "dialogs": "dialogs/ 弹窗(用到才 import)",
    "pages": "pages/ 嵌入页面",
    "monitoring": "monitoring/ 性能监控",
    "tools": "tools/纯逻辑",
    "scripts": "scripts/ 测试脚本",
    "build_tools": "build_tools/ 打包脚本与配置",
}
COLOR = {
    "root": "#FFF2CC", "dialogs": "#E2EFDA", "pages": "#DEEBF7",
    "monitoring": "#FCE4D6", "tools": "#EDEDED", "scripts": "#F2F2F2",
    "build_tools": "#D9EAD3",
}


def collect():
    """返回 (mods, order, edges)。"""
    mods, order = {}, []
    for p in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT)
        mods[p.stem] = p
        order.append((p.stem, rel))

    edges = {s: set() for s, _ in order}
    for stem, rel in order:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8-sig", errors="ignore"))
        except Exception as e:  # noqa: BLE001
            print("PARSE FAIL", rel, e)
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    names = [node.module]
            for nm in names:
                for part in nm.split("."):
                    if part in mods and part != stem:
                        edges[stem].add(part)
    return mods, order, edges


def group_of(rel):
    return "root" if len(rel.parent.parts) == 0 else rel.parent.parts[0]


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_dot(mods, order, edges) -> "tuple[int, int]":
    groups = {}
    for stem, rel in order:
        groups.setdefault(group_of(rel), []).append(stem)
    id_map = {s: f"n{i}" for i, (s, _) in enumerate(order)}

    L = ["digraph Super_ADB_deps {"]
    L.append(f'  graph [fontname="{FONT}" rankdir=TB splines=ortho nodesep=0.3 ranksep=0.7];')
    L.append(f'  node [fontname="{FONT}" fontsize=11 shape=box style=filled '
             f'fillcolor="#F2F6FC" color="#91A8D0"];')
    L.append(f'  edge [fontname="{FONT}" fontsize=9 color="#6B7785" arrowsize=0.7];')
    for gname in ["root", "dialogs", "pages", "monitoring", "tools", "scripts", "build_tools"]:
        if gname not in groups:
            continue
        L.append(f"  subgraph cluster_{gname} {{")
        L.append(f'    label="{esc(LABEL[gname])}"; fontname="{FONT}"; '
                 f'style=filled; color="#B0B0B0"; fillcolor="{COLOR[gname]}";')
        for stem in sorted(groups[gname]):
            L.append(f'    {id_map[stem]} [label="{esc(stem)}.py"];')
        L.append("  }")
    for stem in sorted(edges):
        for dst in sorted(edges[stem]):
            L.append(f"  {id_map[stem]} -> {id_map[dst]};")
    L.append("}")
    DOT.write_text("\n".join(L), encoding="utf-8")
    return len(order), sum(len(v) for v in edges.values())


def render():
    exe = shutil.which("dot") or DOTEXE
    if not os.path.exists(exe):
        print("ERROR: dot not found at", exe)
        sys.exit(2)
    for fmt in ("svg", "png"):
        out = str(BASE.with_suffix("." + fmt))
        r = subprocess.run([exe, f"-T{fmt}", str(DOT), "-o", out],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        print(fmt, "rc=", r.returncode, "->", out)
        if r.stderr.strip():
            print("   stderr:", r.stderr.strip()[:400])


if __name__ == "__main__":
    mods, order, edges = collect()
    n, e = build_dot(mods, order, edges)
    print("nodes =", n, " edges =", e)
    render()
