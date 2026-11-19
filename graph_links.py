# -*- coding: utf-8 -*-
"""Obsidian 图谱连接层：在每日计划与刷题笔记正文里写入真正的 [[双链]]。

背景：Dataview 生成的表格不产生图连线，只有正文里的 wikilink 才会。
所以需要把「计划 ↔ 知识点 ↔ 题目 ↔ 相邻日期」用正文双链串起来。

所有插入内容用 <!-- graph-links --> 包裹，便于幂等更新。
"""
import os, re, io, sys


def _read(path):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def _write_lf(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _strip_delim(text):
    """返回 (frontmatter, body)。无 frontmatter 时 frontmatter 为空串。"""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    return text[:end + 4], text[end + 4:]


# ---------- 计划正文 ----------

def plan_links_block(iso, main_k, related, note_stem, prev_iso, next_iso,
                     station=None, review=False):
    """生成计划正文里的关联区块。"""
    L = ["<!-- graph-links -->"]
    L.append("## 🔗 本日关联")
    L.append("")
    if main_k:
        kn = f"- **知识点**：[[{main_k}]]"
        if related:
            kn += " ｜ 关联：" + " · ".join(f"[[{r}]]" for r in related)
        L.append(kn)
    if note_stem:
        L.append(f"- **刷题笔记**：[[{note_stem}]]")
    if station:
        L.append(f"- **技能站**：{station}")
    nav = []
    if prev_iso:
        nav.append(f"← [[{prev_iso}|前一天]]")
    if next_iso:
        nav.append(f"[[{next_iso}|后一天]] →")
    if nav:
        L.append("- **时间线**：" + " ｜ ".join(nav))
    L.append("")
    L.append("- 总览：[[学习计划总览]] · [[知识点总览]] · [[LeetCode 刷题总览]]")
    L.append("<!-- /graph-links -->")
    return "\n".join(L)


def inject_plan_links(path, iso, main_k, related, note_stem, prev_iso, next_iso,
                       station=None, review=False):
    """把关联区块写进计划正文（幂等）。放在 H1 之后、「## 1.」之前。"""
    if not path or not os.path.exists(path):
        return False
    text = _read(path)
    fm, body = _strip_delim(text)
    block = plan_links_block(iso, main_k, related, note_stem, prev_iso, next_iso,
                             station=station, review=review)

    # 去掉旧区块
    body = re.sub(r"<!-- graph-links -->.*?<!-- /graph-links -->\n*", "", body, flags=re.S)

    lines = body.splitlines()
    insert_at = 0
    # 插在第一个 H1 之后
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            insert_at = i + 1
            # 跳过 H1 后面的空行
            while insert_at < len(lines) and not lines[insert_at].strip():
                insert_at += 1
            break
    new_lines = lines[:insert_at] + [block, ""] + lines[insert_at:]
    new_body = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines))
    new_text = fm + new_body
    if new_text != text:
        _write_lf(path, new_text)
        return True
    return False


# ---------- 刷题笔记正文 ----------

def note_links_block(main_k, related, plan_iso=None, knowledge_names=None):
    """生成笔记正文里的关联区块。"""
    L = ["<!-- graph-links -->"]
    L.append("## 🔗 关联")
    L.append("")
    if main_k:
        L.append(f"- **主知识点**：[[{main_k}]]")
    if related:
        L.append("- **相关知识点**：" + " · ".join(f"[[{r}]]" for r in related))
    if plan_iso:
        L.append(f"- **当日计划**：[[{plan_iso}]]")
    L.append("- **题单**：[[LeetCode 刷题总览]] · [[知识点总览]]")
    L.append("<!-- /graph-links -->")
    return "\n".join(L)


def inject_note_links(path, main_k, related, plan_iso=None):
    """把关联区块追加到笔记正文末尾（幂等）。"""
    if not path or not os.path.exists(path):
        return False
    text = _read(path)
    fm, body = _strip_delim(text)
    body = re.sub(r"\n*<!-- graph-links -->.*?<!-- /graph-links -->\n*", "\n", body, flags=re.S)
    if not body.endswith("\n"):
        body += "\n"
    block = note_links_block(main_k, related, plan_iso)
    new_text = fm + body + "\n" + block + "\n"
    if new_text != text:
        _write_lf(path, new_text)
        return True
    return False
