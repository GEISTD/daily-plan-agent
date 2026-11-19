# -*- coding: utf-8 -*-
"""learning-notes -> Obsidian 增量同步器。

核心原则：**只增不改**。
  已登记在 obsidian_manifest.json 里的文件，一律跳过（那是人工整理过的，
  结构/文件名都调过，不能被自动逻辑覆盖）。
  只有清单里没有的新文件，才按规则表自动投放、补 frontmatter、连主题地图。

新文件的三条出路：
  1. 源目录命中 RULES      -> 进对应学科目录，按主题归类
  2. 命中 KEYWORDS 关键词  -> 猜一个学科
  3. 都猜不到              -> 进 10-学习计划/待归类/，写日志等你确认

用法：
  python obsidian_sync.py             增量同步
  python obsidian_sync.py --dry-run   只报告
  python obsidian_sync.py --adopt     把「待归类」里的文件正式登记进清单
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))


def _find_workspace_root():
    cur = BASE
    for _ in range(6):
        cur = os.path.dirname(cur)
        if not cur or os.path.dirname(cur) == cur:
            break
        if os.path.isdir(os.path.join(cur, "obsidian-notes", ".obsidian")):
            return cur
    return os.path.dirname(os.path.dirname(BASE))


WORKSPACE = os.environ.get("GEIST_ROOT") or _find_workspace_root()
LEARNING = os.path.join(WORKSPACE, "learning-notes")
VAULT = os.path.join(WORKSPACE, "obsidian-notes")
MANIFEST = os.path.join(BASE, "obsidian_manifest.json")
LOG_DIR = os.path.join(BASE, "logs")
OBS_ENABLED = os.path.isdir(os.path.join(VAULT, ".obsidian"))

# 源顶层目录 -> (目标目录, 主题)
RULES = {
    "python_basic":      ("01-Python", "Python"),
    "python":            ("01-Python", "Python"),
    "c_cpp_review":      ("02-C++与Java", "C++"),
    "java_learning":     ("02-C++与Java", "Java"),
    "data structure":    ("03-数据结构与算法", "算法"),
    "leetcode practice": ("03-数据结构与算法/leetcode practice/notes", "算法"),
    "sql":               ("04-SQL", "SQL"),
    "wsl  and linux":    ("05-Linux与工具", "工具"),
    "docker":            ("05-Linux与工具", "工具"),
    "ai":                ("06-AI与机器学习", "AI"),
    "machine-learning":  ("06-AI与机器学习", "机器学习"),
    "quartus":           ("07-数字电路与Quartus", "数字电路"),
    "IELTS":             ("08-英语", "英语"),
    "第一段实习":          ("09-实习与求职", "实习"),
    "resume in grade2":  ("09-实习与求职", "求职"),
    "my learning plan":  ("10-学习计划", "学习计划"),
}

KEYWORDS = [
    (("python", "numpy", "pandas", "django", "flask", "pytest"), ("01-Python", "Python")),
    (("c++", "cpp", "java", "kotlin", "golang", "rust", "qt"), ("02-C++与Java", "C++")),
    (("algorithm", "leetcode", "算法", "数据结构", "排序", "动态规划"), ("03-数据结构与算法", "算法")),
    (("sql", "mysql", "postgres", "database", "数据库", "索引"), ("04-SQL", "SQL")),
    (("linux", "wsl", "shell", "bash", "docker", "k8s", "kubernetes", "git", "vim"), ("05-Linux与工具", "工具")),
    (("ml", "llm", "prompt", "torch", "tensorflow", "numpy", "深度学习",
      "机器学习", "神经网络", "训练"), ("06-AI与机器学习", "机器学习")),
    (("quartus", "verilog", "fpga", "电路", "数电", "modelsim"), ("07-数字电路与Quartus", "数字电路")),
    (("ielts", "toefl", "english", "英语", "雅思", "听力", "阅读"), ("08-英语", "英语")),
    (("resume", "intern", "实习", "简历", "求职", "面试", "offer"), ("09-实习与求职", "求职")),
    (("plan", "计划", "目标", "roadmap", "复盘"), ("10-学习计划", "学习计划")),
]

TOPIC_MAPS = {
    "Python": "Python 学习地图",
    "C++": "C++ 与 Java 学习地图",
    "Java": "C++ 与 Java 学习地图",
    "算法": "算法学习地图",
    "SQL": "SQL 学习地图",
    "工具": "工具链学习地图",
    "AI": "机器学习学习地图",
    "机器学习": "机器学习学习地图",
    "数字电路": "Quartus 实验总览",
    "英语": "IELTS 总览",
    "实习": "求职与实习地图",
    "求职": "求职与实习地图",
    "学习计划": "学习计划总览",
}

SKIP_DIRS = {"daily", "dailyPlan", ".git", ".venv", "venv", "__pycache__",
             ".idea", "node_modules", ".pytest_cache", ".mypy_cache", "build", "dist"}
SKIP_EXT = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".o", ".obj",
            ".class", ".tmp", ".cache", ".log", ".lock"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".ico",
              ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
              ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".mp4", ".wav"}

PENDING_DIR = "10-学习计划/待归类"


def log(msg, echo=True):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"obsidian-sync-{datetime.now():%Y-%m-%d}.log")
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    try:
        with io.open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if echo:
        print(line)


def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            with io.open(MANIFEST, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mapping": {}}


def save_manifest(data):
    with io.open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def classify(rel):
    """返回 (目标目录, 主题) 或 None。"""
    parts = rel.replace("\\", "/").split("/")
    top = parts[0]
    if top in RULES:
        return RULES[top]
    hay = rel.lower()
    for words, mapped in KEYWORDS:
        for w in words:
            if w in hay:
                return mapped
    return None


def read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return None


def title_from(text, path):
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        t = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", m.group(1), re.M)
        if t:
            return t.group(1).strip()
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(path))[0].replace("_", " ")


def sanitize_filename(name):
    """Obsidian 不接受这些字符。"""
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:80] or "未命名"


def add_frontmatter(text, title, topic, rel):
    fm = [
        "---",
        f'title: "{title}"',
        "tags:",
        f"  - {topic}",
        "  - 自动同步",
        "status: 进行中",
        f'topic: "{topic}"',
        f'source: "learning-notes/{rel}"',
        f"synced: {datetime.now():%Y-%m-%d}",
        "---",
        "",
    ]
    return "\n".join(fm) + text


def add_topic_link(text, topic, rel):
    """在正文末尾追加归属链接（图谱连线来源），幂等。"""
    marker_open, marker_close = "<!-- auto-sync-link -->", "<!-- /auto-sync-link -->"
    if marker_open in text:
        text = re.sub(r"\n*" + re.escape(marker_open) + r".*?"
                      + re.escape(marker_close) + r"\n*", "\n", text, flags=re.S)
    mapname = TOPIC_MAPS.get(topic)
    links = ([f"[[{mapname}]]"] if mapname else []) + ["[[📊 学习看板]]", "[[🗂️ 全部笔记索引]]"]
    block = (
        f"{marker_open}\n## 🔗 归属\n\n"
        f"- 主题：{' · '.join(links)}\n"
        f"- 来源：`learning-notes/{rel}`\n"
        f"{marker_close}\n"
    )
    return text.rstrip() + "\n\n" + block


def write_lf(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def iter_new_files(manifest):
    """列出源目录里所有「清单未登记」的文件。"""
    seen = set()
    for dirpath, dirnames, filenames in os.walk(LEARNING):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, LEARNING).replace("\\", "/")
            if rel in manifest["mapping"]:
                seen.add(rel)
                continue
            yield full, rel, ext


def main():
    ap = argparse.ArgumentParser(description="learning-notes -> Obsidian 增量同步")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--adopt", action="store_true",
                    help="把待归类目录里已同步的文件正式登记")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not OBS_ENABLED:
        log(f"[错误] Obsidian 库不存在：{VAULT}")
        return 2

    manifest = load_manifest()

    if args.adopt:
        n = 0
        pending_abs = os.path.join(VAULT, PENDING_DIR)
        if os.path.isdir(pending_abs):
            for fn in os.listdir(pending_abs):
                if not fn.endswith(".md"):
                    continue
                p = os.path.join(pending_abs, fn)
                t = read_text(p) or ""
                m = re.search(r'^source:\s*"learning-notes/(.+?)"', t, re.M)
                if m:
                    manifest["mapping"][m.group(1)] = f"{PENDING_DIR}/{fn}"
                    n += 1
        save_manifest(manifest)
        log(f"已登记 {n} 个待归类文件")
        return 0

    stats = {"md": 0, "bin": 0, "pending": 0, "skipped": 0}
    unknown_tops = {}

    for full, rel, ext in iter_new_files(manifest):
        if args.dry_run and ext not in (".md",) + tuple(BINARY_EXT):
            continue
        if ext not in (".md",) and ext not in BINARY_EXT:
            stats["skipped"] += 1
            continue

        mapped = classify(rel)

        if ext in BINARY_EXT:
            dst = os.path.join(VAULT, "90-附件", sanitize_filename(os.path.basename(rel)))
            if args.dry_run:
                log(f"  [dry] 附件 -> {os.path.relpath(dst, VAULT)}", not args.quiet)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(full, dst)
                stats["bin"] += 1
            continue

        text = read_text(full)
        if text is None:
            log(f"  [skip] 非 UTF-8：{rel}", not args.quiet)
            stats["skipped"] += 1
            continue

        if mapped:
            dest_dir, topic = mapped
            target_rel = f"{dest_dir}/{sanitize_filename(os.path.basename(rel))}"
        else:
            dest_dir, topic = PENDING_DIR, "待归类"
            target_rel = f"{PENDING_DIR}/{sanitize_filename(os.path.basename(rel))}"
            stats["pending"] += 1
            unknown_tops[rel.split("/")[0]] = unknown_tops.get(rel.split("/")[0], 0) + 1

        title = title_from(text, full)
        body = text
        if re.match(r"^---\s*\n", body):
            # 已有 frontmatter：不重写，只补归属链接
            new_text = add_topic_link(body, topic, rel)
        else:
            new_text = add_topic_link(add_frontmatter(body, title, topic, rel), topic, rel)

        dst = os.path.join(VAULT, target_rel)
        if args.dry_run:
            log(f"  [dry] {rel} -> {target_rel}", not args.quiet)
            continue

        # 同名冲突：追加序号
        base, ext2 = os.path.splitext(dst)
        i = 2
        while os.path.exists(dst):
            dst = f"{base}-{i}{ext2}"
            i += 1
        write_lf(dst, new_text)
        manifest["mapping"][rel] = os.path.relpath(dst, VAULT).replace("\\", "/")
        stats["md"] += 1

    if not args.dry_run:
        save_manifest(manifest)

    log(f"同步完成：新笔记 {stats['md']} 篇｜附件 {stats['bin']} 个｜"
        f"待归类 {stats['pending']} 篇｜跳过 {stats['skipped']} 个")
    if unknown_tops:
        log("[需要归类] 这些源目录没匹配到学科，已放进「待归类」：")
        for d, n in sorted(unknown_tops.items()):
            log(f"    {d}/  ({n} 个文件) —— 在 obsidian_sync.py 的 RULES 加一行即可")
    return 0


if __name__ == "__main__":
    sys.exit(main())
