# -*- coding: utf-8 -*-
"""建立 learning-notes -> obsidian-notes 的既有文件映射清单（bootstrap）。

目标：给已在 Obsidian 库里整理好的笔记，补上 source 字段和清单记录，
这样通用同步器以后只处理「清单里没有的新文件」，绝不覆盖现有结构。

匹配策略（从严到宽）：
  1. 相对路径完全相同
  2. 文件名完全相同（唯一时）
  3. 文件名相同（多个候选时，用内容前 200 字相似度挑）
  4. 标题相同（frontmatter title 或 H1）
"""
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(BASE))
LEARNING = os.path.join(WORKSPACE, "learning-notes")
VAULT = os.path.join(WORKSPACE, "obsidian-notes")
MANIFEST = os.path.join(BASE, "obsidian_manifest.json")

SKIP_DIRS = {"daily", "dailyPlan", ".git", ".venv", "venv", "__pycache__",
             ".idea", "node_modules", ".obsidian", ".trash"}


def walk_files(root, exts=None):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if exts and ext not in exts:
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return ""


def norm(s):
    """用于相似度比较的归一化。"""
    s = re.sub(r"[\s\W_]+", "", s.lower())
    return s


def title_of(text, path):
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        t = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", m.group(1), re.M)
        if t:
            return t.group(1).strip()
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(path))[0]


def similarity(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 简单前缀/包含判断足够应付同一篇笔记的改写
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb))
    common = 0
    for ch in set(na):
        if ch in nb:
            common += 1
    return common / max(len(set(na)), 1) * 0.5


def main():
    if not os.path.isdir(LEARNING) or not os.path.isdir(VAULT):
        print("路径不存在")
        return 2

    src_files = walk_files(LEARNING, {".md"})
    dst_files = walk_files(VAULT, {".md"})

    dst_by_rel = {}
    dst_by_name = {}
    for p in dst_files:
        rel = os.path.relpath(p, VAULT).replace("\\", "/")
        dst_by_rel[rel] = p
        dst_by_name.setdefault(os.path.basename(p), []).append(p)

    # 读 dst 内容缓存（标题/正文开头）
    dst_cache = {}
    for p in dst_files:
        t = read_text(p)
        dst_cache[p] = (t, title_of(t, p))

    manifest = {}
    unmatched = []

    for sp in src_files:
        srel = os.path.relpath(sp, LEARNING).replace("\\", "/")
        fname = os.path.basename(sp)
        stext = read_text(sp)
        stitle = title_of(stext, sp)

        hit = None

        # 1) 相对路径完全相同
        if srel in dst_by_rel:
            hit = dst_by_rel[srel]

        # 2) 文件名唯一
        if hit is None:
            cands = dst_by_name.get(fname, [])
            if len(cands) == 1:
                hit = cands[0]
            elif len(cands) > 1:
                # 3) 多候选：按标题+正文相似度挑
                best, best_score = None, 0.0
                for c in cands:
                    ct, ctitle = dst_cache[c]
                    sc = max(similarity(stitle, ctitle),
                             similarity(stext[:400], ct[:400]))
                    if sc > best_score:
                        best, best_score = c, sc
                if best and best_score >= 0.5:
                    hit = best

        # 4) 标题匹配（兜底）
        if hit is None:
            best, best_score = None, 0.0
            for p, (ct, ctitle) in dst_cache.items():
                sc = similarity(stitle, ctitle)
                if sc > best_score:
                    best, best_score = p, sc
            if best and best_score >= 0.85:
                hit = best

        if hit:
            manifest[srel] = os.path.relpath(hit, VAULT).replace("\\", "/")
        else:
            unmatched.append(srel)

    out = {
        "generated": True,
        "note": "learning-notes 相对路径 -> obsidian-notes 相对路径",
        "mapping": manifest,
    }
    with io.open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"源文件 {len(src_files)} 个，已匹配 {len(manifest)} 个，未匹配 {len(unmatched)} 个")
    if unmatched:
        print("\n未匹配（这些是 Obsidian 里没有的，会被当成新文件同步）：")
        for u in unmatched:
            print("  " + u)
    return 0


if __name__ == "__main__":
    sys.exit(main())
