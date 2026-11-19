# -*- coding: utf-8 -*-
"""
每日学习计划 + 手写练习骨架生成器（AI Engineer 深耕串行版）
- 读取内置的技能站时间表与 LeetCode 题库
- 生成：桌面《学习计划_日期.md》+ dailyPlan 仓库 plans/ + practice/ 练习 .py 骨架
- state.json 维护幂等：同一天重复运行不重复出题；题号只在确认后推进
- 确认模式：python gen_daily.py --confirm → 归档全部未确认计划、推进题号、结算连续打卡
- 自定义计划：--new-goal "目标" 调用 LLM 生成逐日计划；--import-plan 导入现成 JSON；--builtin 切回内置
纯标准库，无第三方依赖。
"""
import importlib.util, json, os, random, re, shutil, subprocess, sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    import knowledge_map as KM
except Exception as _km_err:  # 映射表缺失时降级运行，不影响计划生成
    KM = None
    print("knowledge_map.py not available:", _km_err, file=sys.stderr)
LOGS_DIR = os.path.join(BASE, "logs")
STATE_FILE = os.path.join(BASE, "state.json")
# 供 ps1 读取的机器可读结果（Python 直接写 UTF-8，绕过 PS 捕获子进程输出的 GBK 乱码坑）
LAST_RUN = os.path.join(BASE, "last_run.json")


def write_run_result(obj):
    with open(LAST_RUN, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _write_lf(path, text):
    """写文本并强制 LF 换行：Obsidian 库的 .gitattributes 是 `* text=auto eol=lf`，
    用默认 CRLF 写会让每次同步都产生一整片假的 diff。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------- 后台静默执行（Windows 下不弹黑窗） ----------
# 计划任务本身是隐藏运行的，但 Python 调 git.exe / python.exe 这类控制台程序时，
# Windows 仍会为子进程创建控制台窗口并一闪而过。统一加 CREATE_NO_WINDOW 消除。
_NO_WINDOW = 0
if os.name == "nt":
    _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _run_quiet(cmd, timeout=10, **kwargs):
    """执行外部命令且不弹出控制台窗口；返回 subprocess.CompletedProcess。"""
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault("startupinfo", si)
        kwargs.setdefault("creationflags", _NO_WINDOW)
    if "capture_output" not in kwargs and "stdout" not in kwargs:
        kwargs["capture_output"] = True
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    kwargs.setdefault("timeout", timeout)
    return subprocess.run(cmd, **kwargs)


def _find_workspace_root():
    # 向上逐级查找：认准含 .git 的独立归档仓库 dailyPlan，或含 obsidian-notes/.obsidian 的库根，
    # 这样脚本无论是从 learning-notes/daily 还是从 obsidian-notes/10-学习计划/daily 运行都能定位到同一工作区。
    cur = BASE
    for _ in range(5):
        cur = os.path.dirname(cur)
        if not cur or os.path.dirname(cur) == cur:
            break
        if os.path.isdir(os.path.join(cur, "dailyPlan", ".git")):
            return cur
        if (os.path.isdir(os.path.join(cur, "obsidian-notes", ".obsidian"))
                and os.path.isdir(os.path.join(cur, "learning-notes"))):
            return cur
    p1 = os.path.dirname(BASE)
    p2 = os.path.dirname(p1)
    return p2 if os.path.basename(p1) == "learning-notes" else p1


WORKSPACE_ROOT = _find_workspace_root()
# dailyPlan 归档仓库：plans/=进行中 done/=已确认 practice/=手写练习（可用环境变量覆盖）
DAILY_REPO = os.environ.get("DAILY_PLAN_REPO") or os.path.join(WORKSPACE_ROOT, "dailyPlan")
LEARNING_NOTES_DIR = os.environ.get("LEARNING_NOTES_DIR") or os.path.join(WORKSPACE_ROOT, "learning-notes")
LC_NOTES_DIR = os.environ.get("LC_NOTES_DIR") or os.path.join(LEARNING_NOTES_DIR, "leetcode practice")
REPO_PLANS = os.path.join(DAILY_REPO, "plans")
REPO_DONE = os.path.join(DAILY_REPO, "done")
PRAC_DIR = os.path.join(DAILY_REPO, "practice")
for d in (LOGS_DIR, REPO_PLANS, REPO_DONE, PRAC_DIR):
    os.makedirs(d, exist_ok=True)

# ---------- Obsidian 知识库：与上面的仓库并行镜像（存在才启用，不存在则完全跳过） ----------
OBSIDIAN_VAULT = os.environ.get("OBSIDIAN_VAULT") or os.path.join(WORKSPACE_ROOT, "obsidian-notes")
OBS_ENABLED = os.path.isdir(os.path.join(OBSIDIAN_VAULT, ".obsidian"))
OBS_DAILY = os.path.join(OBSIDIAN_VAULT, "10-学习计划", "daily")
OBS_PLANS = os.path.join(OBS_DAILY, "plans")
OBS_DONE = os.path.join(OBS_DAILY, "done")
OBS_PRAC = os.path.join(OBS_DAILY, "practice")
OBS_LC = os.path.join(OBSIDIAN_VAULT, "03-数据结构与算法", "leetcode practice")
OBS_LC_NOTES = os.path.join(OBS_LC, "notes")
OBS_IELTS = os.path.join(OBSIDIAN_VAULT, "08-英语", "IELTS")
if OBS_ENABLED:
    for d in (OBS_PLANS, OBS_DONE, OBS_PRAC, OBS_LC_NOTES):
        os.makedirs(d, exist_ok=True)

# 英语笔记：优先用 Obsidian 库里的 IELTS 周记，其次回退到 learning-notes/IELTS
IELTS_DIR = OBS_IELTS if (OBS_ENABLED and os.path.isdir(OBS_IELTS)) else os.path.join(LEARNING_NOTES_DIR, "IELTS")

# ---------- 桌面路径（兼容 OneDrive 重定向） ----------
def desktop_dir():
    home = os.path.expanduser("~")
    od = os.path.join(home, "OneDrive", "Desktop")
    if os.path.isdir(od):
        return od
    od2 = os.path.join(home, "OneDrive", "桌面")
    if os.path.isdir(od2):
        return od2
    return os.path.join(home, "Desktop")

# ---------- 技能站时间表：(起, 止, 站名, 主任务, 今日手写, AI可帮, 毕业标准) ----------
STAGES = [
 ("2026-10-12","2026-10-13","S1 Python/NumPy","逻辑回归数学：BCE损失+MLE推导（1页推导笔记，嵌在项目里）",
  "手推 BCE 梯度（标量→矩阵），写推导笔记","检查推导是否正确","10-25 ML from Scratch 一条命令跑通3模型"),
 ("2026-10-14","2026-10-18","S1 Python/NumPy","NumPy 手写逻辑回归：sigmoid/BCE/梯度下降/predict，乳腺癌数据集跑通，算准确率/精确率/召回率/混淆矩阵",
  "sigmoid、BCE、梯度、混淆矩阵全部手写（不用 sklearn）","数据加载调试、matplotlib 画决策边界","同上"),
 ("2026-10-19","2026-10-22","S1 Python/NumPy","NumPy 手写 kNN + 特征归一化 + 留一法交叉验证，画 k=1..15 准确率曲线",
  "距离计算、投票逻辑、归一化手写","画准确率曲线","同上"),
 ("2026-10-23","2026-10-25","S1 Python/NumPy","整合 ML from Scratch 仓库：统一接口+英文 README+demo.py 一条命令跑通 3 模型；周日做首次 Demo Day",
  "README 用法示例自己写并验证","README 润色、仓库结构建议","同上"),
 ("2026-10-26","2026-11-01","S2 PyTorch","autograd 本质：把 S1 线性回归用纯 tensor+autograd 重写（不用 nn.Linear），反向传播推导 1 页",
  "手推反向传播链；手写训练循环","对比新旧代码差异","11-22 CIFAR10≥70%+误差分析报告"),
 ("2026-11-02","2026-11-08","S2 PyTorch","nn.Module + Dataset/DataLoader：自定义 Dataset 类 + MLP 分类（make_moons 双月牙）",
  "Dataset/__getitem__/训练循环手写","可视化决策边界","同上"),
 ("2026-11-09","2026-11-15","S2 PyTorch","CNN + 训练工程：CIFAR10 卷积网络（卷积/池化/BatchNorm），学习率调度+早停+TensorBoard",
  "CNN 结构定义、训练与评估循环手写","数据增强建议、调参脚本","同上"),
 ("2026-11-16","2026-11-22","S2 PyTorch","收尾：误差分析+混淆矩阵+3 类失败案例+2 页报告+Demo Day",
  "混淆矩阵计算复用 S1 手写版","报告排版、失败样本可视化","同上"),
 ("2026-11-23","2026-11-29","S3 Transformer/HF","attention 手写：PyTorch 写 Q/K/V 缩放点积注意力（小矩阵验证），自注意力公式推导 1 页",
  "Q/K/V 计算、softmax 注意力、掩码全部手写","公式排版检查","12-06 attention 数值正确+BERT demo"),
 ("2026-11-30","2026-12-06","S3 Transformer/HF","HuggingFace 微调：BERT 中文评论情感分类，先手写训练循环再用 Trainer 对比",
  "先手写微调循环（复用 S2 能力）","数据集下载、Trainer 配置、报错处理","同上"),
 ("2026-12-07","2026-12-13","S4 检索(RAG前半)","文本处理：手写分块器（固定长度+overlap+句子边界）；手写 TF-IDF + 余弦相似度 baseline",
  "分块算法、TF-IDF、余弦相似度全手写","语料来源整理","12-31 recall@3≥70%"),
 ("2026-12-14","2026-12-20","S4 检索(RAG前半)","embedding 语义检索：调 embedding 模型，手写向量检索，与 TF-IDF baseline 对比 10 个测试问题",
  "向量归一化、相似度排序、Top-K 手写","embedding 选型调研、API 用法","同上"),
 ("2026-12-21","2026-12-27","S4 检索(RAG前半)","重排+标注：手写混合重排（关键词+语义加权）；构建 golden dataset（20 条问答+正确来源）",
  "重排加权逻辑手写","标注格式建议","同上"),
 ("2026-12-28","2026-12-31","S4 检索(RAG前半)","检索模块整合+评估：手写 recall@k，出评估报告（检查点：RAG 检索毕业）",
  "recall@k 计算手写","评估结果可视化","同上"),
 ("2027-01-01","2027-01-10","S5 应用工程(RAG后半)","接 LLM 生成：手写 3 种 prompt 模板对比实验；答案带引用来源；手写 faithfulness 判定",
  "prompt 模板、引用拼接、faithfulness 判定","LLM API 选型、成本估算","01-31 RAG 公网 URL 可演示"),
 ("2027-01-11","2027-01-17","S5 应用工程(RAG后半)","FastAPI：手写问答路由+Pydantic 模型+错误处理+.env 密钥管理+异步接口",
  "路由、请求/响应模型、异常处理手写","依赖安装排查","同上"),
 ("2027-01-18","2027-01-24","S5 应用工程(RAG后半)","Docker+部署（Docker/Linux 本周用时学）：手写 Dockerfile+compose，部署到云服务器，公网可访问",
  "Dockerfile 逐行自己写","云控制台操作、安全组配置","同上"),
 ("2027-01-25","2027-01-31","S5 应用工程(RAG后半)","收尾：README+架构图+2min 演示视频+5 类失败分析报告+Demo Day",
  "用法示例亲自验证","架构图、视频剪辑、英文 README","同上"),
 ("2027-02-01","2027-02-07","S6 Agent+面试","Function Calling：手写 tool-use 循环（查文档→算数值→回答），加成本与延迟日志",
  "tool 调用循环、参数校验、日志记录","工具 schema 设计建议","02-28 简历+30问+模拟面试达标"),
 ("2027-02-08","2027-02-14","S6 Agent+面试","LangChain 重构对比：检索+生成用框架重写，产出对比报告（可选 LoRA 体验）",
  "对比实验评估跑分（复用手写评估）","LangChain 用法上手","同上"),
 ("2027-02-15","2027-02-21","S6 Agent+面试","简历 v1 + 3 个项目 STAR 讲解稿 + 30 问概念卡",
  "讲解稿自己写初稿","简历排版润色、概念卡核对","同上"),
 ("2027-02-22","2027-02-28","S6 Agent+面试","模拟面试 1 次（45min）+ 检查点验收：RAG 在线演示 + 3 个被问住的点补上",
  "不看稿讲完 3 分钟项目版","模拟面试官","同上"),
 ("2027-03-01","2027-03-14","阶段4 投递+GRE","收集 10 份 JD 提炼共性 + 搭建 4 个投递渠道 + 第一轮投递 10 份",
  "算法转向错题复盘（Hot100 简单+中等）","JD 匹配分析、简历定制建议","05-23 投递≥40+GRE 320+3.5"),
 ("2027-03-15","2027-04-25","阶段4 投递+GRE","每周一轮投递+跟进（累计≥40）；模拟面试≥2；GRE 备考每日 1.5h",
  "错题重做 20 题，中等题 30min 内写出可运行解","面经整理、GRE 数学练习","同上"),
 ("2027-04-26","2027-05-23","阶段4 投递+GRE","GRE 模考 + 正式考试（目标 320+3.5）；阶段 4 验收",
  "保持每天 1 题手感不断线","—","同上"),
 ("2027-06-01","2027-06-21","阶段5 面试+入职","面试密集期 + Offer 决策（每场 24h 内复盘）",
  "面试手撕代码全部本地重写一遍","—","07-31 ≥1 offer"),
 ("2027-06-22","2027-07-12","阶段5 面试+入职","入职实习：认领完整小模块+实习日志；与 2-3 位推荐人正式沟通",
  "两周内交付一个可演示小模块","—","同上"),
 ("2027-07-13","2027-07-31","阶段5 面试+入职","PS 素材库 10 条 + 托福/雅思报名（2027 Q4 考）+ 全周期验收",
  "亲手整理 10 条 STAR 素材","—","同上"),
]

WEEKDAYS = ["周一","周二","周三","周四","周五","周六","周日"]

# 周内主任务拆解：把整周相同的阶段大目标拆解为每天具体的推进节奏
WEEKDAY_FOCUS = [
    ("周一 · 原理拆解与骨架搭建", "厘清本周核心数学原理与输入输出接口，搭好代码目录与数据加载骨架"),
    ("周二 · 核心模块手写攻关", "专注亲手实现「必须手写」中最核心的算法/网络层/梯度或损失逻辑，先跑通小样本"),
    ("周三 · 完整流程与基线跑通", "串联完整训练/推理与评估循环，在真实数据集上跑出第一版 baseline 指标"),
    ("周四 · 调优与对比实验", "对比参考实现或开展超参/消融实验（如学习率调度、正则化、不同配置），记录指标变化"),
    ("周五 · 误差分析与代码收尾", "统计混淆矩阵与失败案例，整理注释与 README 用法示例，确保一条命令可复现"),
    ("周六 · 可视化产出日联动", "提取本周手写代码的真实运行数据，完成下方「🎨 周末产出日」图表/动画"),
    ("周日 · 脱稿重写与周验收", "完成下方「周日特殊任务」（核心代码重写 + 产出墙归档），对照本站毕业标准自检"),
]

DIFF_ZH = {"Easy": "简单", "Medium": "中等", "Hard": "困难"}


def latest_ielts_file():
    """查找 learning-notes/IELTS/ 下最新的 week*.md 笔记路径。"""
    if not os.path.isdir(IELTS_DIR):
        return r"D:\GEIST\learning-notes\IELTS"
    best_num, best_path = -1, None
    for name in os.listdir(IELTS_DIR):
        if name.lower().endswith((".md", ".txt")):
            m = re.search(r"(\d+)", name)
            w = int(m.group(1)) if m else 0
            if w >= best_num:
                best_num, best_path = w, os.path.join(IELTS_DIR, name)
    return best_path or IELTS_DIR


def _note_stores():
    """返回需要同步的刷题笔记库列表：learning-notes 原库 + Obsidian 库（存在才加）。"""
    stores = []
    if os.path.isdir(LC_NOTES_DIR):
        stores.append({"root": LC_NOTES_DIR, "style": "readme"})
    if OBS_ENABLED and os.path.isdir(OBS_LC_NOTES):
        stores.append({"root": OBS_LC, "style": "obsidian"})
    return stores


def _knowledge_links(qnum, topic):
    """返回 (主知识点, [相关知识点])；映射表缺失时降级为空，不影响计划生成。"""
    if KM is None:
        return None, []
    try:
        main, related = KM.knowledge_for(qnum, topic)
        return main, [r for r in related if r != main]
    except Exception:
        return None, []


def _note_body(qmeta, topic, iso, prac_path, status="进行中"):
    num, qname, slug, diff, tags, sig, hint, _ = qmeta
    diff_zh = DIFF_ZH.get(diff, diff)
    main_k, rel_k = _knowledge_links(num, topic)
    if "design" in tags:
        code_stub = f"{sig}\n    pass"
    else:
        code_stub = f"class Solution:\n    {sig}\n        pass"
    head = [
        f"- 链接：https://leetcode.cn/problems/{slug}/",
        f"- 难度：{diff_zh}",
        f"- 标签：{topic}",
        f"- 日期：{iso}",
        f"- 状态：{status}",
        f"- 本地文件：`{prac_path}`",
    ]
    if main_k:
        head.append(f"- 知识点：[[{main_k}]]")
    return head + [
        "",
        "## 题目",
        "",
        "（用自己的话复述题目要求，包括输入、输出、约束条件）",
        "",
        "## 我的理解",
        "",
        "（这题到底在问什么？边界情况有哪些？）",
        "",
        "## 第一版思路",
        "",
        "（先自己写，写清楚打算怎么做、为什么这么做）",
        "",
        "## 卡点 / 我问的问题",
        "",
        "1. ",
        "2. ",
        "",
        "## AI 讲解",
        "",
        "（关键点是什么？原思路哪里不对？正确思路是怎么一步步想到的？）",
        "",
        "## 代码",
        "",
        "```python",
        code_stub,
        "```",
        "",
        "- 时间复杂度：",
        "- 空间复杂度：",
        "",
        "## 一句话总结",
        "",
        "（下次遇到同类题，第一反应应该想什么）",
        "",
        "## 复习标记",
        "",
        "- 第一次重做：还没做",
        "- 是否能不看答案写出来：",
        "",
    ]


def _obsidian_frontmatter(qmeta, topic, iso, status="进行中"):
    num, qname, slug, diff = qmeta[0], qmeta[1], qmeta[2], qmeta[3]
    diff_zh = DIFF_ZH.get(diff, diff)
    tag_lines = ["  - leetcode"]
    for t in str(topic).replace("、", ",").split(","):
        t = t.strip()
        if t and t != "leetcode":
            tag_lines.append(f"  - {t}")
    main_k, rel_k = _knowledge_links(num, topic)
    extra = []
    if main_k:
        # knowledge 只放主知识点（一个），保证统计不重复计数；
        # 关联知识点单独放 knowledge_related，只用于导航，不参与题数统计。
        extra.append("knowledge:")
        extra.append(f'  - "{main_k}"')
        if rel_k:
            extra.append("knowledge_related:")
            for r in rel_k:
                extra.append(f'  - "{r}"')
    return [
        "---",
        f'title: "{num}. {qname}"',
        "aliases:",
        f'  - "{qname}"',
        "tags:",
        *tag_lines,
        f"difficulty: {diff_zh}",
        f"status: {status}",
        f"date: {iso}",
        f"url: https://leetcode.cn/problems/{slug}/",
        *extra,
        "---",
    ]


def ensure_lc_note(qmeta, topic, iso, prac_path):
    """同时在 learning-notes 原库与 Obsidian 库中确保当日主练题笔记存在，并在索引里登记。

    Obsidian 侧写成带 YAML frontmatter 的格式，让 Dataview / 学习看板能自动统计；
    learning-notes 侧维持原来的纯 Markdown 版本 + README 进度表。
    """
    num, qname = qmeta[0], qmeta[1]
    diff_zh = DIFF_ZH.get(qmeta[3], qmeta[3])
    first_path = None

    for store in _note_stores():
        root = store["root"]
        notes_sub = os.path.join(root, "notes")
        os.makedirs(notes_sub, exist_ok=True)
        note_fname = f"{num:04d}-{qname}.md"
        note_path = os.path.join(notes_sub, note_fname)
        if first_path is None:
            first_path = note_path

        body = _note_body(qmeta, topic, iso, prac_path)
        if not os.path.exists(note_path):
            if store["style"] == "obsidian":
                lines = _obsidian_frontmatter(qmeta, topic, iso) + [f"# {num}. {qname}", ""]
                _write_lf(note_path, "\n".join(lines + body))
            else:
                lines = [f"# {num}. {qname}", ""]
                with open(note_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines + body))

        # 索引登记：learning-notes 是手写 Markdown 表格；Obsidian 用 Dataview 自动汇总，无需插行
        if store["style"] == "readme":
            readme_path = os.path.join(root, "README.md")
            if os.path.exists(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8-sig") as f:
                        content = f.read()
                    row_key = f"| {num:04d} |"
                    new_row = (f"| {num:04d} | [{qname}](notes/{note_fname}) | "
                               f"{diff_zh} | {topic} | 进行中 | {iso} |")
                    if row_key not in content:
                        lines = content.splitlines()
                        insert_at = None
                        for i, line in enumerate(lines):
                            if line.strip().startswith("|---"):
                                insert_at = i + 1
                            elif insert_at is not None and line.strip().startswith("|"):
                                insert_at = i + 1
                            elif insert_at is not None and not line.strip().startswith("|"):
                                break
                        if insert_at is not None:
                            lines.insert(insert_at, new_row)
                            with open(readme_path, "w", encoding="utf-8") as f:
                                f.write("\n".join(lines) + "\n")
                except Exception as e:
                    print("lc readme update failed:", e, file=sys.stderr)
    return first_path


def _extract_user_code(prac_path):
    """从练习文件中提取手写实现代码（若仍是初始 pass 模板则返回 None）。"""
    if not prac_path or not os.path.exists(prac_path):
        return None
    try:
        with open(prac_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except Exception:
        return None
    marker = "# ---- 本地测试"
    idx = text.find(marker)
    body = text[:idx] if idx > 0 else text
    m = re.search(r"^(class\s+\w+[\s\S]+)", body, re.MULTILINE)
    if not m:
        return None
    code_block = m.group(1).strip()
    # 去掉 docstring 和注释后看是否只有 class/def/pass
    stripped = re.sub(r'""".*?"""', "", code_block, flags=re.DOTALL)
    meaningful = [
        ln.strip() for ln in stripped.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
        and not ln.strip().startswith("class ")
        and not ln.strip().startswith("def ")
    ]
    if not meaningful or meaningful == ["pass"]:
        return None
    return code_block


def _find_practice_file(iso, num, slug):
    """查找某日某题对应的练习文件路径。"""
    mm_dd = iso[5:]
    candidates = [
        f"{mm_dd}_lc{num}_{slug}_review.py",
        f"{mm_dd}_lc{num}_{slug}.py",
    ]
    for c in candidates:
        p = os.path.join(PRAC_DIR, c)
        if os.path.exists(p):
            return p, c
    prefix = f"{mm_dd}_lc{num}_{slug}"
    if os.path.isdir(PRAC_DIR):
        for fn in os.listdir(PRAC_DIR):
            if fn.startswith(prefix) and fn.endswith(".py"):
                return os.path.join(PRAC_DIR, fn), fn
    return os.path.join(PRAC_DIR, candidates[1]), candidates[1]


def _set_note_state(note_path, num, qname, status, iso=None, review=False, user_code=None,
                    obsidian=False):
    """把一份笔记的状态/代码写回；同时兼容 learning-notes 纯列表格式与 Obsidian YAML frontmatter 格式。"""
    if not note_path or not os.path.exists(note_path):
        return
    try:
        with open(note_path, "r", encoding="utf-8-sig") as f:
            nt = f.read()
        if obsidian:
            nt = re.sub(r"^status:.*$", f"status: {status}", nt, flags=re.MULTILINE)
            nt = re.sub(r"^- 状态：.*$", f"- 状态：{status}", nt, flags=re.MULTILINE)
        else:
            nt = re.sub(r"^- 状态：.*$", f"- 状态：{status}", nt, flags=re.MULTILINE)
        if review:
            nt = re.sub(r"^- 第一次重做：还没做\s*$",
                        f"- 第一次重做：{iso} 已复习重做", nt, flags=re.MULTILINE)
        if user_code:
            nt = re.sub(
                r"(## 代码\s+```python\n)([\s\S]*?)(\n```)",
                lambda m: m.group(1) + user_code + m.group(3),
                nt,
                count=1,
            )
        if obsidian:
            _write_lf(note_path, nt)
        else:
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(nt)
    except Exception as e:
        print("update lc note failed:", e, file=sys.stderr)


def mark_lc_note_ac(qi, iso, review=False):
    """确认归档时：把对应题目在 learning-notes 与 Obsidian 两个笔记库中的状态同步为 AC，并写回手写代码。"""
    qmeta = qmeta_of(qi)
    topic = lc_entry(qi)[0]
    num, qname, slug = qmeta[0], qmeta[1], qmeta[2]
    prac_path, _ = _find_practice_file(iso, num, slug)
    ensure_lc_note(qmeta, topic, iso, prac_path)   # 确保两个库的笔记文件都已存在
    user_code = _extract_user_code(prac_path)
    note_fname = f"{num:04d}-{qname}.md"

    for store in _note_stores():
        note_path = os.path.join(store["root"], "notes", note_fname)
        _set_note_state(note_path, num, qname, "AC", iso=iso, review=review,
                        user_code=user_code, obsidian=(store["style"] == "obsidian"))
        if store["style"] == "readme":
            readme_path = os.path.join(store["root"], "README.md")
            if os.path.exists(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8-sig") as f:
                        rc = f.read()
                    pattern = (rf"^(\|\s*{num:04d}\s*\|[^|]+\|[^|]+\|[^|]+\|\s*)"
                               rf"(?:进行中|未开始)(\s*\|[^|]+\|)$")
                    rc_new = re.sub(pattern, r"\1AC\2", rc, flags=re.MULTILINE)
                    if rc_new != rc:
                        with open(readme_path, "w", encoding="utf-8") as f:
                            f.write(rc_new)
                except Exception as e:
                    print("mark lc readme failed:", e, file=sys.stderr)

# ---------- LeetCode 题库：(题号, 题名, slug, 难度, 标签, 方法签名, 思路提示, 用例) ----------
# 标签: ll=链表 tree=二叉树 design=设计题 set_eq=无序结果集 lol_eq=无序二维列表
Q = [
 (283,"移动零","move-zeroes","Easy",{"inplace"},"def moveZeroes(self, nums: List[int]) -> None:  # 原地修改",
  "快慢双指针：慢指针指向下一个非零应放的位置，快指针扫描；最后把慢指针之后全置 0",
  [([[0,1,0,3,12]],[1,3,12,0,0])]),
 (26,"删除有序数组中的重复项","remove-duplicates-from-sorted-array","Easy",set(),"def removeDuplicates(self, nums: List[int]) -> int:",
  "快慢指针，慢指针存唯一元素的边界，返回慢指针+1",[([[1,1,2]],2)]),
 (27,"移除元素","remove-element","Easy",set(),"def removeElement(self, nums: List[int], val: int) -> int:",
  "双指针原地覆盖：遇到不等于 val 的就写到慢指针位置",[([[3,2,2,3],3],2)]),
 (167,"两数之和 II","two-sum-ii-input-array-is-sorted","Medium",set(),"def twoSum(self, numbers: List[int], target: int) -> List[int]:",
  "对撞双指针：和小了左指针右移，大了右指针左移（返回下标从 1 开始）",[([[2,7,11,15],9],[1,2])]),
 (11,"盛最多水的容器","container-with-most-water","Medium",set(),"def maxArea(self, height: List[int]) -> int:",
  "对撞双指针，面积=短板×宽；每次移动较短的板才可能变大",[([[1,8,6,2,5,4,8,3,7]],49)]),
 (977,"有序数组的平方","squares-of-a-sorted-array","Easy",set(),"def sortedSquares(self, nums: List[int]) -> List[int]:",
  "双指针从两端向中间比较绝对值，结果数组从后往前填",[([[-4,-1,0,3,10]],[0,1,9,16,100])]),
 (209,"长度最小的子数组","minimum-size-subarray-sum","Medium",set(),"def minSubArrayLen(self, target: int, nums: List[int]) -> int:",
  "滑动窗口：右指针扩张，窗口和≥target 时左指针收缩并更新最小长度",[([7,[2,3,1,2,4,3]],2)]),
 (3,"无重复字符的最长子串","longest-substring-without-repeating-characters","Medium",set(),"def lengthOfLongestSubstring(self, s: str) -> int:",
  "滑动窗口+集合/哈希记录窗口内字符；重复时左指针收缩到无重复",[(["abcabcbb"],3)]),
 (15,"三数之和","3sum","Medium",{"lol_eq"},"def threeSum(self, nums: List[int]) -> List[List[int]]:",
  "排序后固定一个数，剩下用对撞双指针；三处去重（固定位、左右指针）",[([[-1,0,1,2,-1,-4]],[[-1,-1,2],[-1,0,1]])]),
 (1,"两数之和","two-sum","Easy",set(),"def twoSum(self, nums: List[int], target: int) -> List[int]:",
  "哈希表扫一遍：查 target-x 是否已在表中，在则返回，不在则存入",[([[2,7,11,15],9],[0,1])]),
 (206,"反转链表","reverse-linked-list","Easy",{"ll"},"def reverseList(self, head: Optional[ListNode]) -> Optional[ListNode]:",
  "三指针迭代 pre/cur/nxt：逐个断开指向 pre；或递归",[([[1,2,3,4,5]],[5,4,3,2,1])]),
 (21,"合并两个有序链表","merge-two-sorted-lists","Easy",{"ll"},"def mergeTwoLists(self, list1: Optional[ListNode], list2: Optional[ListNode]) -> Optional[ListNode]:",
  "哑节点 dummy + 双指针逐个接较小节点，剩余直接接上",[([[1,2,4],[1,3,4]],[1,1,2,3,4,4])]),
 (141,"环形链表","linked-list-cycle","Easy",{"ll"},"def hasCycle(self, head: Optional[ListNode]) -> bool:",
  "快慢指针（1步/2步），相遇即有环；None 则无环",[([[3,2,0,-4],1],True),([[1],-1],False)]),
 (876,"链表的中间结点","middle-of-the-linked-list","Easy",{"ll"},"def middleNode(self, head: Optional[ListNode]) -> Optional[ListNode]:",
  "快慢指针同起点，快到尾时慢在中间（偶数个取第二个中间）",[([[1,2,3,4,5]],[3,4,5])]),
 (20,"有效的括号","valid-parentheses","Easy",set(),"def isValid(self, s: str) -> bool:",
  "栈：左括号入栈，右括号检查栈顶是否匹配；最后栈空才合法",[(["()[]{}"],True),(["(]"],False)]),
 (155,"最小栈","min-stack","Medium",{"design"},"class MinStack:  # push/pop/top/getMin",
  "辅助栈：与主栈同步 push，栈顶永远是当前最小值（可存 (val,min) 元组）",
  ["s = MinStack(); s.push(-2); s.push(0); s.push(-3)",
   "assert s.getMin() == -3",
   "s.pop(); assert s.top() == 0 and s.getMin() == -2"]),
 (232,"用栈实现队列","implement-queue-using-stacks","Easy",{"design"},"class MyQueue:  # push/peek/pop/empty",
  "双栈：in 栈管入队，peek/pop 时若 out 栈空则把 in 全部倒进 out",
  ["q = MyQueue(); q.push(1); q.push(2)",
   "assert q.peek() == 1 and q.pop() == 1 and not q.empty()"]),
 (739,"每日温度","daily-temperatures","Medium",set(),"def dailyTemperatures(self, temperatures: List[int]) -> List[int]:",
  "单调递减栈存下标；当前温度更高时弹栈，距离=下标差",[([[73,74,75,71,69,72,76,73]],[1,1,4,2,1,1,0,0])]),
 (225,"用队列实现栈","implement-stack-using-queues","Easy",{"design"},"class MyStack:  # push/top/pop/empty",
  "单队列：每次 push 后把前面的元素依次出队再入队，新元素就到了队首",
  ["s = MyStack(); s.push(1); s.push(2)",
   "assert s.top() == 2 and s.pop() == 2 and not s.empty()"]),
 (42,"接雨水","trapping-rain-water","Hard",set(),"def trap(self, height: List[int]) -> int:",
  "对撞双指针+左右最大高度：每次处理较矮一侧（选做，先理解按列求 min(左max,右max)-h）",[([[0,1,0,2,1,0,1,3,2,1,2,1]],6)]),
 (49,"字母异位词分组","group-anagrams","Medium",{"lol_eq"},"def groupAnagrams(self, strs: List[str]) -> List[List[str]]:",
  "排序后的字符串作为哈希 key 分组（或用 26 字母计数元组）",[([["eat","tea","tan","ate","nat","bat"]],[["ate","eat","tea"],["bat"],["nat","tan"]])]),
 (349,"两个数组的交集","intersection-of-two-arrays","Easy",{"set_eq"},"def intersection(self, nums1: List[int], nums2: List[int]) -> List[int]:",
  "两个 set 求交集即可",[([[1,2,2,1],[2,2]],[2]),([[4,9,5],[9,4,9,8,4]],[4,9])]),
 (128,"最长连续序列","longest-consecutive-sequence","Medium",set(),"def longestConsecutive(self, nums: List[int]) -> int:",
  "哈希集合；只从 x-1 不存在的起点开始数 x+1,x+2…，保证 O(n)",[([[100,4,200,1,3,2]],4)]),
 (202,"快乐数","happy-number","Easy",set(),"def isHappy(self, n: int) -> bool:",
  "哈希集合检测循环：重复出现且不为 1 就不快乐（也可用快慢指针）",[([19],True),([2],False)]),
 (350,"两个数组的交集 II","intersection-of-two-arrays-ii","Easy",{"set_eq"},"def intersect(self, nums1: List[int], nums2: List[int]) -> List[int]:",
  "哈希表计数：短表计数，长表每匹配一个就计数-1 并加入答案",[([[1,2,2,1],[2,2]],[2,2]),([[4,9,5],[9,4,9,8,4]],[4,9])]),
 (704,"二分查找","binary-search","Easy",set(),"def search(self, nums: List[int], target: int) -> int:",
  "标准闭区间二分 [l,r]：l<=r，mid=(l+r)//2，找不到返回 -1",[([[-1,0,3,5,9,12],9],4)]),
 (35,"搜索插入位置","search-insert-position","Easy",set(),"def searchInsert(self, nums: List[int], target: int) -> int:",
  "二分找第一个 ≥ target 的位置；全部小于 target 则返回 len",[([[1,3,5,6],5],2)]),
 (34,"查找元素首尾位置","find-first-and-last-position-of-element-in-sorted-array","Medium",set(),"def searchRange(self, nums: List[int], target: int) -> List[int]:",
  "两次二分：一次找左边界（≥target 第一个），一次找右边界（≤target 最后一个）",[([[5,7,7,8,8,10],8],[3,4]),([[5,7,7,8,8,10],6],[-1,-1])]),
 (33,"搜索旋转排序数组","search-in-rotated-sorted-array","Medium",set(),"def search(self, nums: List[int], target: int) -> int:",
  "二分时判断哪半边有序（nums[mid] 与 nums[l] 比较），再决定 target 是否在有序半边",[([[4,5,6,7,0,1,2],0],4),([[4,5,6,7,0,1,2],3],-1)]),
 (69,"x 的平方根","sqrtx","Easy",set(),"def mySqrt(self, x: int) -> int:",
  "二分答案 [0,x]：mid²≤x 记录答案并向右找，否则向左",[([8],2),([4],2)]),
 (144,"二叉树前序遍历","binary-tree-preorder-traversal","Easy",{"tree"},"def preorderTraversal(self, root: Optional[TreeNode]) -> List[int]:",
  "根-左-右；迭代版用栈，注意右孩子先入栈",[([[1,None,2,3]],[1,2,3])]),
 (94,"二叉树中序遍历","binary-tree-inorder-traversal","Easy",{"tree"},"def inorderTraversal(self, root: Optional[TreeNode]) -> List[int]:",
  "左-根-右；迭代：一路向左压栈，弹出访问，再转右孩子",[([[1,None,2,3]],[1,3,2])]),
 (102,"二叉树层序遍历","binary-tree-level-order-traversal","Medium",{"tree"},"def levelOrder(self, root: Optional[TreeNode]) -> List[List[int]]:",
  "BFS 队列，每轮处理当前层全部节点，按层收集",[([[3,9,20,None,None,15,7]],[[3],[9,20],[15,7]])]),
 (104,"二叉树最大深度","maximum-depth-of-binary-tree","Easy",{"tree"},"def maxDepth(self, root: Optional[TreeNode]) -> int:",
  "递归：max(左子树深, 右子树深)+1；空节点 0",[([[3,9,20,None,None,15,7]],3)]),
 (226,"翻转二叉树","invert-binary-tree","Easy",{"tree"},"def invertTree(self, root: Optional[TreeNode]) -> Optional[TreeNode]:",
  "递归交换每个节点的左右孩子（前序/后序均可）",[([[4,2,7,1,3,6,9]],[4,7,2,9,6,3,1])]),
 (101,"对称二叉树","symmetric-tree","Easy",{"tree"},"def isSymmetric(self, root: Optional[TreeNode]) -> bool:",
  "递归比较两棵子树：外侧对外侧、内侧对内侧，值要相等",[([[1,2,2,3,4,4,3]],True)]),
 (543,"二叉树的直径","diameter-of-binary-tree","Easy",{"tree"},"def diameterOfBinaryTree(self, root: Optional[TreeNode]) -> int:",
  "后序：每个节点答案=左深+右深，返回 max(左,右)+1；用 nonlocal 维护最大值",[([[1,2,3,4,5]],3)]),
 (110,"平衡二叉树","balanced-binary-tree","Easy",{"tree"},"def isBalanced(self, root: Optional[TreeNode]) -> bool:",
  "后序返回高度；左右高度差>1 返回 -1 表示不平衡并一路上传",[([[3,9,20,None,None,15,7]],True)]),
 (236,"最近公共祖先","lowest-common-ancestor-of-a-binary-tree","Medium",{"tree"},"def lowestCommonAncestor(self, root: 'TreeNode', p: 'TreeNode', q: 'TreeNode') -> 'TreeNode':",
  "后序递归：左右子树各找到一个 → 当前节点就是 LCA；自己命中则返回自己",[([[3,5,1,6,2,0,8,None,None,7,4]],3)]),
 (112,"路径总和","path-sum","Easy",{"tree"},"def hasPathSum(self, root: Optional[TreeNode], targetSum: int) -> bool:",
  "DFS 每经过节点减去其值，到叶子判剩余是否为 0",[([[5,4,8,11,None,13,4,7,2,None,None,None,1],22],True)]),
 (124,"二叉树最大路径和","binary-tree-maximum-path-sum","Hard",{"tree"},"def maxPathSum(self, root: Optional[TreeNode]) -> int:",
  "后序：贡献值=node.val+max(0,左贡献,右贡献)；经过当前节点的路径=node+左贡献+右贡献",[([[1,2,3]],6),([[-10,9,20,None,None,15,7]],42)]),
 (200,"岛屿数量","number-of-islands","Medium",set(),"def numIslands(self, grid: List[List[str]]) -> int:",
  "遍历网格，遇到 '1' 计数+1 并 DFS/BFS 把相邻陆地全部淹没成 '0'",[([[["1","1","0","0","0"],["1","1","0","0","0"],["0","0","1","0","0"],["0","0","0","1","1"]]],3)]),
 (695,"岛屿的最大面积","max-area-of-islands","Medium",set(),"def maxAreaOfIsland(self, grid: List[List[int]]) -> int:",
  "DFS 返回当前岛屿面积（1+四方向之和），全程维护最大值",[([[[0,0,1,0,0],[0,1,1,1,0],[0,0,0,0,0]]],4)]),
 (994,"腐烂的橘子","rotting-oranges","Medium",set(),"def orangesRotting(self, grid: List[List[int]]) -> int:",
  "多源 BFS：所有腐烂橘子同时入队，按分钟层序扩散；最后还有新鲜橘子返回 -1",[([[[2,1,1],[1,1,0],[0,1,1]]],4),([[[2,1,1],[0,1,1],[1,0,1]]],-1)]),
 (207,"课程表","course-schedule","Medium",set(),"def canFinish(self, numCourses: int, prerequisites: List[List[int]]) -> bool:",
  "拓扑排序：建邻接表+入度数组，入度 0 入队，出队即学完并解锁后续；全部学完=无环",[([2,[[1,0]]],True),([2,[[1,0],[0,1]]],False)]),
 (70,"爬楼梯","climbing-stairs","Easy",set(),"def climbStairs(self, n: int) -> int:",
  "dp[i]=dp[i-1]+dp[i-2]；只需两个变量滚动，O(1) 空间",[([2],2),([3],3),([4],5)]),
 (198,"打家劫舍","house-robber","Medium",set(),"def rob(self, nums: List[int]) -> int:",
  "dp：当前房 max(不偷=上一家最优, 偷=上上家+当前)；两个变量滚动",[([[1,2,3,1]],4)]),
 (53,"最大子数组和","maximum-subarray","Medium",set(),"def maxSubArray(self, nums: List[int]) -> int:",
  "以 i 结尾的最大和=max(nums[i], 之前最优+nums[i])，滚动维护全局最大",[([[-2,1,-3,4,-1,2,1,-5,4]],6)]),
 (322,"零钱兑换","coin-change","Medium",set(),"def coinChange(self, coins: List[int], amount: int) -> int:",
  "完全背包：dp[金额]=最少硬币数，初始化无穷大，逐硬币更新",[([[1,2,5],11],3),([[2],3],-1)]),
 (62,"不同路径","unique-paths","Medium",set(),"def uniquePaths(self, m: int, n: int) -> int:",
  "二维 dp：dp[i][j]=dp[i-1][j]+dp[i][j-1]，首行首列全 1",[([3,7],28)]),
 (1143,"最长公共子序列","longest-common-subsequence","Medium",set(),"def longestCommonSubsequence(self, text1: str, text2: str) -> int:",
  "二维 dp：字符相等取对角+1，否则取上/左的较大值",[(["abcde","ace"],3),(["abc","def"],0)]),
 (46,"全排列","permutations","Medium",{"lol_eq"},"def permute(self, nums: List[int]) -> List[List[int]]:",
  "回溯 + used 数组：每层选一个未使用元素，撤销选择",[([[1,2,3]],[[1,2,3],[1,3,2],[2,1,3],[2,3,1],[3,1,2],[3,2,1]])]),
 (78,"子集","subsets","Medium",{"lol_eq"},"def subsets(self, nums: List[int]) -> List[List[int]]:",
  "回溯：每个元素选或不选，每次进入递归都收集当前路径",[([[1,2,3]],[[],[1],[2],[3],[1,2],[1,3],[2,3],[1,2,3]])]),
 (39,"组合总和","combination-sum","Medium",{"lol_eq"},"def combinationSum(self, candidates: List[int], target: int) -> List[List[int]]:",
  "回溯+可重复选（start 不递增）；先排序便于剩余值为负时剪枝",[([[2,3,6,7],7],[[2,2,3],[7]])]),
]

# SQL 暂缓（用户要求后置；恢复时按 LeetCode Database 题号顺序 175→181→… 接续安排）
SQL_DAYS = {}

# ---------- LC 闯关模式：主任务 = LeetCode 主题闯关（AI 主任务/SQL 暂缓） ----------
# 每天 1 道主练（在 Q 中有骨架/提示/用例）+ 2 道举一反三（同主题，网页直接做）
LC_FOCUS = True
# (主题, 主练题号, [(题号, 题名, slug, 难度), (题号, 题名, slug, 难度)])
LC_PLAN = [
 ("双指针", 283, [(344,"反转字符串","reverse-string","Easy"),(125,"验证回文串","valid-palindrome","Easy")]),
 ("双指针", 26, [(88,"合并两个有序数组","merge-sorted-array","Easy"),(392,"判断子序列","is-subsequence","Easy")]),
 ("双指针", 27, [(455,"分发饼干","assign-cookies","Easy"),(905,"按奇偶排序数组","sort-array-by-parity","Easy")]),
 ("双指针", 977, [(345,"反转字符串中的元音字母","reverse-vowels-of-a-string","Easy"),(941,"有效的山脉数组","valid-mountain-array","Easy")]),
 ("双指针", 167, [(121,"买卖股票的最佳时机","best-time-to-buy-and-sell-stock","Easy"),(122,"买卖股票的最佳时机 II","best-time-to-buy-and-sell-stock-ii","Medium")]),
 ("双指针", 11, [(16,"最接近的三数之和","3sum-closest","Medium"),(18,"四数之和","4sum","Medium")]),
 ("双指针", 15, [(454,"四数相加 II","4sum-ii","Medium"),(532,"数组中的 k-diff 数对","k-diff-pairs-in-an-array","Medium")]),
 ("滑动窗口", 209, [(643,"子数组最大平均数 I","maximum-average-subarray-i","Easy"),(438,"找到字符串中所有字母异位词","find-all-anagrams-in-a-string","Medium")]),
 ("滑动窗口", 3, [(567,"字符串的排列","permutation-in-string","Medium"),(219,"存在重复元素 II","contains-duplicate-ii","Easy")]),
 ("哈希表", 1, [(217,"存在重复元素","contains-duplicate","Easy"),(242,"有效的字母异位词","valid-anagram","Easy")]),
 ("哈希表", 49, [(383,"赎金信","ransom-note","Easy"),(136,"只出现一次的数字","single-number","Easy")]),
 ("哈希表", 349, [(268,"丢失的数字","missing-number","Easy"),(448,"找到所有数组中消失的数字","find-all-numbers-disappeared-in-an-array","Easy")]),
 ("哈希表", 350, [(387,"字符串中的第一个唯一字符","first-unique-character-in-a-string","Easy"),(205,"同构字符串","isomorphic-strings","Easy")]),
 ("哈希表", 202, [(290,"单词规律","word-pattern","Easy"),(409,"最长回文串","longest-palindrome","Easy")]),
 ("哈希表", 128, [(380,"O(1)时间插入删除获取随机元素","insert-delete-getrandom-o1","Medium"),(299,"猜数字游戏","bulls-and-cows","Medium")]),
 ("二分查找", 704, [(278,"第一个错误的版本","first-bad-version","Easy"),(374,"猜数字大小","guess-number-higher-or-lower","Easy")]),
 ("二分查找", 35, [(367,"有效的完全平方数","valid-perfect-square","Easy"),(50,"Pow(x, n)","powx-n","Medium")]),
 ("二分查找", 69, [(153,"寻找旋转排序数组中的最小值","find-minimum-in-rotated-sorted-array","Medium"),(162,"寻找峰值","find-peak-element","Medium")]),
 ("二分查找", 34, [(74,"搜索二维矩阵","search-a-2d-matrix","Medium"),(81,"搜索旋转排序数组 II","search-in-rotated-sorted-array-ii","Medium")]),
 ("二分查找", 33, [(875,"爱吃香蕉的珂珂","koko-eating-bananas","Medium"),(1011,"在 D 天内送达包裹的能力","capacity-to-ship-packages-within-d-days","Medium")]),
 ("链表", 206, [(203,"移除链表元素","remove-linked-list-elements","Easy"),(83,"删除排序链表中的重复元素","remove-duplicates-from-sorted-list","Easy")]),
 ("链表", 21, [(234,"回文链表","palindrome-linked-list","Easy"),(160,"相交链表","intersection-of-two-linked-lists","Easy")]),
 ("链表", 141, [(142,"环形链表 II","linked-list-cycle-ii","Medium"),(287,"寻找重复数","find-the-duplicate-number","Medium")]),
 ("链表", 876, [(19,"删除链表的倒数第 N 个结点","remove-nth-node-from-end-of-list","Medium"),(148,"排序链表","sort-list","Medium")]),
 ("栈与队列", 20, [(682,"棒球比赛","baseball-game","Easy"),(844,"比较含退格的字符串","backspace-string-compare","Easy")]),
 ("栈与队列", 155, [(496,"下一个更大元素 I","next-greater-element-i","Easy"),(503,"下一个更大元素 II","next-greater-element-ii","Medium")]),
 ("栈与队列", 232, [(150,"逆波兰表达式求值","evaluate-reverse-polish-notation","Medium"),(71,"简化路径","simplify-path","Medium")]),
 ("栈与队列", 225, [(622,"设计循环队列","design-circular-queue","Medium"),(641,"设计循环双端队列","design-circular-deque","Medium")]),
 ("栈与队列", 739, [(901,"股票价格跨度","online-stock-span","Medium"),(856,"括号的分数","score-of-parentheses","Medium")]),
 ("栈与队列", 42, [(84,"柱状图中最大的矩形","largest-rectangle-in-histogram","Hard"),(85,"最大矩形","maximal-rectangle","Hard")]),
 ("二叉树DFS", 144, [(100,"相同的树","same-tree","Easy"),(572,"另一棵树的子树","subtree-of-another-tree","Easy")]),
 ("二叉树DFS", 94, [(257,"二叉树的所有路径","binary-tree-paths","Easy"),(111,"二叉树的最小深度","minimum-depth-of-binary-tree","Easy")]),
 ("二叉树DFS", 104, [(108,"将有序数组转换为二叉搜索树","convert-sorted-array-to-binary-search-tree","Easy"),(559,"N 叉树的最大深度","maximum-depth-of-n-ary-tree","Easy")]),
 ("二叉树DFS", 226, [(617,"合并二叉树","merge-two-binary-trees","Easy"),(404,"左叶子之和","sum-of-left-leaves","Easy")]),
 ("二叉树DFS", 101, [(222,"完全二叉树的节点个数","count-complete-tree-nodes","Medium"),(671,"二叉树中第二小的节点","second-minimum-node-in-a-binary-tree","Easy")]),
 ("二叉树DFS", 110, [(563,"二叉树的坡度","binary-tree-tilt","Easy"),(687,"最长同值路径","longest-univalue-path","Medium")]),
 ("二叉树DFS", 543, [(437,"路径总和 III","path-sum-iii","Medium"),(98,"验证二叉搜索树","validate-binary-search-tree","Medium")]),
 ("二叉树DFS", 112, [(113,"路径总和 II","path-sum-ii","Medium"),(129,"求根节点到叶节点数字之和","sum-root-to-leaf-numbers","Medium")]),
 ("二叉树DFS", 236, [(235,"二叉搜索树的最近公共祖先","lowest-common-ancestor-of-a-binary-search-tree","Easy"),(701,"二叉搜索树中的插入操作","insert-into-a-binary-search-tree","Medium")]),
 ("二叉树DFS", 124, [(297,"二叉树的序列化与反序列化","serialize-and-deserialize-binary-tree","Hard"),(968,"监控二叉树","binary-tree-cameras","Hard")]),
 ("二叉树BFS", 102, [(637,"二叉树的层平均值","average-of-levels-in-binary-tree","Easy"),(107,"二叉树的层序遍历 II","binary-tree-level-order-traversal-ii","Medium")]),
 ("图与BFS", 200, [(733,"图像渲染","flood-fill","Easy"),(463,"岛屿的周长","island-perimeter","Easy")]),
 ("图与BFS", 695, [(130,"被围绕的区域","surrounded-regions","Medium"),(1254,"统计封闭岛屿的数目","number-of-closed-islands","Medium")]),
 ("图与BFS", 994, [(1162,"地图分析","as-far-from-land-as-possible","Medium"),(542,"01 矩阵","01-matrix","Medium")]),
 ("图与BFS", 207, [(210,"课程表 II","course-schedule-ii","Medium"),(997,"找到小镇的法官","find-the-town-judge","Easy")]),
 ("回溯", 78, [(77,"组合","combinations","Medium"),(90,"子集 II","subsets-ii","Medium")]),
 ("回溯", 46, [(47,"全排列 II","permutations-ii","Medium"),(17,"电话号码的字母组合","letter-combinations-of-a-phone-number","Medium")]),
 ("回溯", 39, [(22,"括号生成","generate-parentheses","Medium"),(40,"组合总和 II","combination-sum-ii","Medium")]),
 ("动态规划", 70, [(746,"使用最小花费爬楼梯","min-cost-climbing-stairs","Easy"),(118,"杨辉三角","pascals-triangle","Easy")]),
 ("动态规划", 53, [(152,"乘积最大子数组","maximum-product-subarray","Medium"),(918,"环形子数组的最大和","maximum-sum-circular-subarray","Medium")]),
 ("动态规划", 198, [(213,"打家劫舍 II","house-robber-ii","Medium"),(337,"打家劫舍 III","house-robber-iii","Medium")]),
 ("动态规划", 62, [(63,"不同路径 II","unique-paths-ii","Medium"),(64,"最小路径和","minimum-path-sum","Medium")]),
 ("动态规划", 1143, [(300,"最长递增子序列","longest-increasing-subsequence","Medium"),(583,"两个字符串的删除操作","delete-operation-for-two-strings","Medium")]),
 ("动态规划", 322, [(91,"解码方法","decode-ways","Medium"),(279,"完全平方数","perfect-squares","Medium")]),
]
QN2I = {q[0]: i for i, q in enumerate(Q)}

# 选做加练池：按主题分组、每关独立一对（顺序与 LC_PLAN 中该主题各关的出现顺序一一对应），
# 保证每天选做题也不同。状态好就做满 5 题（≈50-60min），做完 3 题即达标
BONUS = {
 "双指针": [
  [(922,"按奇偶排序数组 II","sort-array-by-parity-ii","Easy"),(917,"仅仅反转字母","reverse-only-letters","Easy")],
  [(925,"长按键入","long-pressed-name","Easy"),(680,"验证回文串 II","valid-palindrome-ii","Easy")],
  [(849,"到最近的人的最大距离","maximize-distance-to-closest-person","Medium"),(986,"区间列表的交集","interval-list-intersections","Medium")],
  [(881,"救生艇","boats-to-save-people","Medium"),(611,"有效三角形的个数","valid-triangle-number","Medium")],
  [(75,"颜色分类","sort-colors","Medium"),(633,"平方数之和","sum-of-square-numbers","Medium")],
  [(713,"乘积小于 K 的子数组","subarray-product-less-than-k","Medium"),(1099,"小于 K 的两数之和","two-sum-less-than-k","Easy")],
  [(1498,"满足条件的子序列数目","number-of-subsequences-that-satisfy-the-given-sum-condition","Medium"),(825,"适龄的朋友","friends-of-appropriate-ages","Medium")],
 ],
 "滑动窗口": [
  [(1456,"定长子串中元音的最大数目","maximum-number-of-vowels-in-a-substring-of-given-length","Medium"),(187,"重复的DNA序列","repeated-dna-sequences","Medium")],
  [(904,"水果成篮","fruit-into-baskets","Medium"),(1004,"最大连续1的个数 III","max-consecutive-ones-iii","Medium")],
 ],
 "哈希表": [
  [(771,"宝石与石头","jewels-and-stones","Easy"),(389,"找不同","find-the-difference","Easy")],
  [(451,"根据字符出现频率排序","sort-characters-by-frequency","Medium"),(500,"键盘行","keyboard-row","Easy")],
  [(575,"分糖果","distribute-candies","Easy"),(1207,"独一无二的出现次数","unique-number-of-occurrences","Easy")],
  [(599,"两个列表的最小索引总和","minimum-index-sum-of-two-lists","Easy"),(748,"最短补全词","shortest-completing-word","Easy")],
  [(645,"错误的集合","set-mismatch","Easy"),(720,"词典中最长的单词","longest-word-in-dictionary","Medium")],
  [(692,"前K个高频单词","top-k-frequent-words","Medium"),(525,"连续数组","contiguous-array","Medium")],
 ],
 "二分查找": [
  [(441,"排列硬币","arranging-coins","Easy"),(852,"山脉数组的峰顶索引","peak-index-in-a-mountain-array","Medium")],
  [(1351,"统计有序矩阵中的负数","count-negative-numbers-in-a-sorted-matrix","Easy"),(2089,"找出数组排序后的目标下标","find-target-indices-after-sorting-array","Easy")],
  [(154,"寻找旋转排序数组中的最小值 II","find-minimum-in-rotated-sorted-array-ii","Medium"),(540,"有序数组中的单一元素","single-element-in-a-sorted-array","Medium")],
  [(436,"寻找右区间","find-right-interval","Medium"),(981,"基于时间的键值存储","time-based-key-value-store","Medium")],
  [(1283,"使结果不超过阈值的最小除数","find-the-smallest-divisor-given-a-threshold","Medium"),(410,"分割数组的最大值","split-array-largest-sum","Hard")],
 ],
 "链表": [
  [(92,"反转链表 II","reverse-linked-list-ii","Medium"),(445,"两数相加 II","add-two-numbers-ii","Medium")],
  [(2,"两数相加","add-two-numbers","Medium"),(24,"两两交换链表中的节点","swap-nodes-in-pairs","Medium")],
  [(143,"重排链表","reorder-list","Medium"),(147,"对链表进行插入排序","insertion-sort-list","Medium")],
  [(61,"旋转链表","rotate-list","Medium"),(86,"分隔链表","partition-list","Medium")],
 ],
 "栈与队列": [
  [(1047,"删除字符串中的所有相邻重复项","remove-all-adjacent-duplicates-in-string","Easy"),(394,"字符串解码","decode-string","Medium")],
  [(1472,"设计浏览器历史记录","design-browser-history","Medium"),(1381,"设计一个支持增量操作的栈","design-a-stack-with-increment-operation","Medium")],
  [(341,"扁平化嵌套列表迭代器","flatten-nested-list-iterator","Medium"),(735,"小行星碰撞","asteroid-collision","Medium")],
  [(1670,"设计前中后队列","design-front-middle-back-queue","Medium"),(933,"最近的请求次数","number-of-recent-calls","Easy")],
  [(402,"移掉 K 位数字","remove-k-digits","Medium"),(636,"函数的独占时间","exclusive-time-of-functions","Medium")],
  [(907,"子数组的最小值之和","sum-of-subarray-minimums","Medium"),(1019,"链表中的下一个更大节点","next-greater-node-in-linked-list","Medium")],
 ],
 "二叉树DFS": [
  [(669,"修剪二叉搜索树","trim-a-binary-search-tree","Medium"),(1038,"从二叉搜索树到更大和树","convert-bst-to-greater-tree","Medium")],
  [(897,"递增顺序搜索树","increasing-order-search-tree","Easy"),(700,"二叉搜索树中的搜索","search-in-a-binary-search-tree","Easy")],
  [(965,"单值二叉树","univalued-binary-tree","Easy"),(606,"根据二叉树创建字符串","construct-string-from-binary-tree","Medium")],
  [(938,"二叉搜索树的范围和","range-sum-of-bst","Easy"),(530,"二叉搜索树的最小绝对差","minimum-absolute-difference-in-bst","Easy")],
  [(958,"二叉树的完全性检验","check-completeness-of-a-binary-tree","Medium"),(513,"找树左下角的值","find-bottom-left-tree-value","Medium")],
  [(508,"出现次数最多的子树元素和","most-frequent-subtree-sum","Medium"),(623,"在二叉树中增加一行","add-one-row-to-tree","Medium")],
  [(814,"二叉树剪枝","binary-tree-pruning","Medium"),(979,"在二叉树中分配硬币","distribute-coins-in-binary-tree","Medium")],
  [(988,"从叶结点开始的最小字符串","smallest-string-starting-from-leaf","Medium"),(1022,"从根到叶的二进制数之和","sum-of-root-to-leaf-binary-numbers","Easy")],
  [(450,"删除二叉搜索树中的节点","delete-node-in-a-bst","Medium"),(1305,"两棵二叉搜索树中的所有元素","all-elements-in-two-binary-search-trees","Medium")],
  [(1372,"二叉树中的最长交错路径","longest-zigzag-path-in-a-binary-tree","Medium"),(1373,"二叉搜索子树的最大键值和","maximum-sum-bst-in-binary-tree","Hard")],
 ],
 "二叉树BFS": [
  [(515,"在每个树行中找最大值","find-largest-value-in-each-tree-row","Medium"),(199,"二叉树的右视图","binary-tree-right-side-view","Medium")],
 ],
 "图与BFS": [
  [(841,"钥匙和房间","keys-and-rooms","Medium"),(133,"克隆图","clone-graph","Medium")],
  [(1091,"二进制矩阵中的最短路径","shortest-path-in-binary-matrix","Medium"),(934,"最短的桥","shortest-bridge","Medium")],
  [(785,"判断二分图","is-graph-bipartite","Medium"),(1926,"迷宫中离入口最近的出口","nearest-exit-from-entrance-in-maze","Medium")],
  [(802,"找到最终的安全状态","find-eventual-safe-states","Medium"),(684,"冗余连接","redundant-connection","Medium")],
 ],
 "回溯": [
  [(784,"字母大小写全排列","letter-case-permutation","Medium"),(131,"分割回文串","palindrome-partitioning","Medium")],
  [(967,"连续差相同的数字","numbers-with-same-consecutive-differences","Medium"),(491,"非递减子序列","non-decreasing-subsequences","Medium")],
  [(216,"组合总和 III","combination-sum-iii","Medium"),(79,"单词搜索","word-search","Medium")],
 ],
 "动态规划": [
  [(509,"斐波那契数","fibonacci-number","Easy"),(1137,"第 N 个泰波那契数","n-th-tribonacci-number","Easy")],
  [(1014,"最佳观光组合","best-sightseeing-pair","Medium"),(1186,"删除一次得到子数组最大和","maximum-subarray-sum-with-one-deletion","Medium")],
  [(740,"删除并获得点数","delete-and-earn","Medium"),(790,"多米诺和托米诺平铺","domino-and-tromino-tiling","Medium")],
  [(931,"下降路径最小和","minimum-falling-path-sum","Medium"),(576,"出界的路径数","out-of-boundary-paths","Medium")],
  [(516,"最长回文子序列","longest-palindromic-subsequence","Medium"),(712,"两个字符串的最小ASCII删除和","minimum-ascii-delete-sum-for-two-strings","Medium")],
  [(377,"组合总和 Ⅳ","combination-sum-iv","Medium"),(96,"不同的二叉搜索树","unique-binary-search-trees","Medium")],
 ],
}

# ---------- 周末产出日：周六/周日每日任务后追加"可视化产出"项目 ----------
# 键 = 技能站名关键词（按顺序取第一个命中，命中站名）；无命中用 DEFAULT_WEEKEND。
# 每个值 = 产出项目对列表，按 ISO 周数轮换（每周六/周日的项目不重复）。
# 每对 = (周六项目, 周日项目)；字段：(项目名, 产出内容[强制复用已学知识], 成就感交付物[含 {W}=ISO周数])。
DEFAULT_WEEKEND = (
    ("📊 本周成果数据海报",
     "把本周所有数字成果用 matplotlib 画成一张大图：损失曲线、准确率、错误案例统计（数据全部来自本周手写代码的真实输出，不许编造）。",
     "交付：`learning-notes/产出墙/week{W}_成果海报.png` —— 发 GitHub/朋友圈，这就是你的进度铁证"),
    ("🗺 本周知识地图",
     "把本周核心概念、手写代码模块、踩过的坑画成一张思维导图（可手绘拍照，或 HTML/CSS 做），每个知识点标注对应代码文件。",
     "交付：`learning-notes/产出墙/week{W}_知识地图.png` —— 明天花 3 分钟向 AI 讲一遍，讲得清才算会"),
)
WEEKEND_OUTPUT = {
    "NumPy": (
        ("📈 手写模型训练曲线动画",
         "跑通手写逻辑回归，把每次迭代的损失用 matplotlib 画成动画/GIF，标注收敛点，并叠加 sklearn 参考虚线对比。复用：sigmoid/BCE/梯度下降/predict 全部手写代码。",
         "交付：`learning-notes/产出墙/week{W}_logreg_curve.gif` + 写 3 行话解释它为什么收敛"),
        ("🖼 三模型效果对比图",
         "乳腺癌数据集上并排画：手写逻辑回归决策边界 / kNN 决策边界 / 混淆矩阵热力图，写 5 行话解释为什么结果不同。复用：S1 手写模型全部代码 + matplotlib。",
         "交付：`learning-notes/产出墙/week{W}_model_compare.png` —— 这是『我能手写机器学习』的最强证据"),
    ),
    "PyTorch": [
        (
            ("🎥 模型训练过程可视化",
             "把 PyTorch 训练循环每 10 步的损失和准确率收集起来，画成动画 GIF；再画 MLP/CNN 在 make_moons/CIFAR10 上的决策边界或特征图。复用：S2 全部手写训练循环 + S1 的 matplotlib 技能。",
             "交付：`learning-notes/产出墙/week{W}_training_anim.gif` + 标注损失下降的 3 个阶段"),
            ("🧠 网络结构 & 梯度流图",
             "用你手写的反向传播推导，把 MLP/CNN 每层的形状变化、梯度大小画成一张结构图（可 draw.io 或 matplotlib），与 autograd 结果对比。复用：手推反向传播 + autograd 实验。",
             "交付：`learning-notes/产出墙/week{W}_net_arch.png` —— 面试画图题直接用它"),
        ),
        (
            ("🔬 分类混淆矩阵 & 错分样本图鉴",
             "用真实跑完的验证集预测结果，手写计算混淆矩阵热力图，并挑出置信度最高的 Top-9 错分样本拼成一张图，标注模型为什么认错。复用：S1 手写混淆矩阵 + S2 训练评估循环。",
             "交付：`learning-notes/产出墙/week{W}_error_gallery.png` + 3 条误差规律分析"),
            ("📊 消融实验 & 学习率调度对比图",
             "对比两组真实训练实验（如加/不加 BatchNorm、或不同学习率调度）的训练/验证曲线与最终指标表。复用：S2 卷积/MLP 训练工程。",
             "交付：`learning-notes/产出墙/week{W}_ablation_compare.png` —— 实验报告核心配图"),
        ),
    ],
    "Transformer": (
        ("🔥 Attention 热力图",
         "用小矩阵跑通你手写的注意力，把 Q·K^T / softmax 结果画成热力图；再对 BERT 真实句子可视化 attention。复用：手写 Q/K/V、缩放点积注意力、掩码全部代码。",
         "交付：`learning-notes/产出墙/week{W}_attention_heatmap.png` + 一句话解释模型在看什么"),
        ("📉 微调收敛对比图",
         "把『手写训练循环』和『Trainer』两条 loss 曲线画在一起对比收敛速度，并附 3 行结论。复用：S2 手写训练循环 + S3 微调实验。",
         "交付：`learning-notes/产出墙/week{W}_finetune_compare.png` —— 写进简历项目描述"),
    ),
    "检索": (
        ("⚖ TF-IDF vs 语义检索对比图",
         "把 10 个测试问题在 TF-IDF 和 embedding 两种检索上的 recall@k 画成柱状图/折线图，标出各自赢的场景。复用：手写分块、TF-IDF、余弦相似度、向量检索全部代码。",
         "交付：`learning-notes/产出墙/week{W}_retrieval_compare.png` + 5 行分析"),
        ("📊 重排效果收益图",
         "画混合重排前后 recall@k 的收益曲线（关键词+语义加权 vs 单一检索），标注在哪些 query 上重排救回来了。复用：手写重排加权 + recall@k 评估。",
         "交付：`learning-notes/产出墙/week{W}_rerank_gain.png` —— RAG 检索毕业检查点材料"),
    ),
    "应用工程": (
        ("🌐 FastAPI 接口演示页",
         "给你手写的 FastAPI 问答服务做一张可交互演示页（HTML + JS 调 API），截图展示带引用的回答和错误处理。复用：Pydantic 模型 + 异步接口 + 错误处理全部手写。",
         "交付：`learning-notes/产出墙/week{W}_api_demo.png`（+ 录屏 GIF），公网 URL 一起放进 README"),
        ("🚀 部署架构图 & 实测报告",
         "画 Docker 部署架构图（客户端→网关→FastAPI→检索→LLM），附公网实测响应时间表。复用：Dockerfile/compose 手写 + 云部署全流程。",
         "交付：`learning-notes/产出墙/week{W}_deploy_arch.png` —— 简历架构图素材"),
    ),
    "Agent": (
        ("🤖 Tool-use 调用流程图",
         "把你手写的 tool 调用循环（查文档→算数值→回答）画成流程图，标注每步的输入输出和成本/延迟日志。复用：tool-use 循环 + 参数校验 + 日志全部手写。",
         "交付：`learning-notes/产出墙/week{W}_tool_flow.png` + 一次真实调用的日志截图"),
        ("⚔ LangChain 对比雷达图",
         "手写版 vs LangChain 版在『代码量/可维护性/性能/延迟』四维打分，画雷达图，附 5 行结论。复用：手写检索+生成 vs 框架重构对比实验。",
         "交付：`learning-notes/产出墙/week{W}_langchain_compare.png` —— 面试『为什么不用框架』的标准答案"),
    ),
    "投递": (
        ("📮 投递进度看板",
         "把 40+ 份投递记录画成可视化看板：按渠道/状态/回复率分组（可用 matplotlib 或 HTML 表格）。复用：Python 数据处理 + 可视化。",
         "交付：`learning-notes/产出墙/week{W}_apply_board.png` —— 每周更新，越画越多越有底气"),
        ("📚 错题 & GRE 复习墙",
         "把本周算法错题和 GRE 生词画成 A4 复习海报（错题思路 + 生词 + 例句），打印贴墙。复用：错题复盘 + GRE 单词积累。",
         "交付：`learning-notes/产出墙/week{W}_review_wall.png` —— 考前扫一眼的都是自己踩过的坑"),
    ),
    "面试": (
        ("🎙 STAR 素材卡片墙",
         "把积累的 10 条 STAR 素材每条画成一张卡片（S/T/A/R 四格 + 数字结果），A4 排版打印。复用：亲手整理的 STAR 素材库。",
         "交付：`learning-notes/产出墙/week{W}_star_cards.png` —— 面试前 10 分钟过一遍"),
        ("🏆 全周期成果时间线",
         "把从 S1 到现在的全部产出画成一条时间线（阶段→产出→链接），做你的『学习主页』。复用：全部已学知识 + 产出墙素材。",
         "交付：`learning-notes/产出墙/week{W}_timeline.png` —— 这就是你的求职作品集封面"),
    ),
}

def weekend_project(d, station):
    """周六/周日产出项目：按技能站关键词匹配 + 按 ISO 周数轮换（每周不重复）。"""
    w = d.isocalendar()[1]
    pairs = DEFAULT_WEEKEND
    for key, val in WEEKEND_OUTPUT.items():
        if key in station:
            pairs = val
            break
    # 兼容两种结构：[(sat1, sun1), (sat2, sun2)] 或单对 (sat, sun)
    if isinstance(pairs, list):
        pair = pairs[w % len(pairs)]
    else:
        pair = pairs
    title, body, deliver = pair[0 if d.weekday() == 5 else 1]
    return title, body, deliver.format(W=w)

def qmeta_of(qi):
    """由闯关序号取主练题元数据。"""
    return Q[QN2I[LC_PLAN[qi % len(LC_PLAN)][1]]]

def lc_entry(qi):
    return LC_PLAN[qi % len(LC_PLAN)]

# ---------- Python 基础营（暂替算法闯关，9 关走完后自动恢复 LeetCode） ----------
PY_CAMP_DIR = os.environ.get("PY_CAMP_DIR") or r"D:\GEIST\learning-notes\python_basic"


def py_camp_status():
    """读取 python_basic/check.py 的真实进度（不是自报，是真跑测试）。

    返回 None（目录不可用）或 dict：
      all_done / done / total / index(当前关0基) / name / title / pending / passed / count
    """
    check_py = os.path.join(PY_CAMP_DIR, "check.py")
    if not os.path.exists(check_py):
        return None
    try:
        spec = importlib.util.spec_from_file_location("py_basic_check", check_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        stages = mod.stage_status()
    except Exception as e:
        print("py camp status failed:", e, file=sys.stderr)
        return None
    if not stages:
        return None

    total = len(stages)
    done = sum(1 for s in stages if s["total"] and s["passed"] == s["total"])
    cur = next((i for i, s in enumerate(stages)
                if not (s["total"] and s["passed"] == s["total"])), total - 1)
    s = stages[cur]
    return {"all_done": done == total, "done": done, "total": total, "index": cur,
            "name": s["name"], "title": s["title"], "pending": s["pending"],
            "passed": s["passed"], "count": s["total"], "error": s["error"],
            "stages": stages}

# ---------- 自定义计划（LLM 生成 / 手动导入，配置存 plan_config.json） ----------
CONFIG_FILE = os.path.join(BASE, "plan_config.json")
DEFAULT_BG = "大二计算机专业学生，有 C/C++ 基础，正在系统学习 AI 方向（Python/NumPy/PyTorch/机器学习）"

def load_plan_cfg():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {"mode": "builtin"}

def save_plan_cfg(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def llm_cfg(cfg):
    llm = dict(cfg.get("llm") or {})
    llm.setdefault("base_url", "https://api.deepseek.com")
    llm.setdefault("model", "deepseek-chat")
    if not llm.get("api_key"):
        llm["api_key"] = os.environ.get("LLM_API_KEY", "")
    return llm

def llm_chat(cfg, messages, timeout=180):
    """OpenAI 兼容 /chat/completions 调用（纯标准库）。"""
    import urllib.request
    llm = llm_cfg(cfg)
    if not llm["api_key"]:
        raise RuntimeError(
            "未配置 LLM API Key。方式1：在 plan_config.json 的 llm.api_key 填写；"
            "方式2：设置环境变量 LLM_API_KEY；"
            "方式3：用 --import-plan 导入任何聊天机器人生成的计划 JSON（无需 API）")
    body = json.dumps({"model": llm["model"], "messages": messages,
                       "temperature": 0.4, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + llm["api_key"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]

def extract_json(text):
    try:
        return json.loads(text)
    except Exception:
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            return json.loads(text[i:j + 1])
        raise ValueError("LLM 输出不是有效 JSON")

PLAN_PROMPT = """把下面的学习目标拆解成逐日学习计划。

【学习目标】{goal}
【周期】{start} 至 {end}（共 {ndays} 天，每天约 {minutes} 分钟）
【学习者背景】{bg}

要求：
1. 划分为 3-8 个阶段，由浅入深，每个阶段有可检验的毕业标准（grad）。
2. 逐日安排：每天任务具体可执行、一句话说清；理论与实践交替；每周至少 1 天复习/重写日；周末任务适当减轻；最后一天为综合实战日。
3. 每天字段：stage(阶段名)、main(主任务)、hand(必须亲手完成的部分)、ai(可交给 AI 辅助的部分)、extra(20-40 分钟附加小任务，可为空字符串)。
4. 只输出 JSON（不要 markdown 代码块、不要任何解释），结构：
{{"milestones":[{{"stage":"阶段名","end":"YYYY-MM-DD","grad":"毕业标准"}}],"days":[{{"stage":"...","main":"...","hand":"...","ai":"...","extra":"..."}}]}}
days 数组长度必须等于 {ndays}，顺序即日期顺序（第一天是 {start}）。"""

def normalize_plan(data, start, end):
    """把 LLM 输出规整为固定结构：日期按顺序强制对齐，缺失字段给默认值。"""
    ndays = (end - start).days + 1
    raw = data.get("days") or []
    if len(raw) < max(1, int(ndays * 0.8)):
        raise ValueError(f"天数不足：{len(raw)}/{ndays}")
    days = []
    for i in range(ndays):
        s = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        days.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "stage": str(s.get("stage") or "综合推进"),
            "main":  str(s.get("main") or s.get("task") or "（按本阶段主题继续推进）"),
            "hand":  str(s.get("hand") or "核心部分亲手实现"),
            "ai":    str(s.get("ai") or "调试、资料查询、代码 review"),
            "extra": str(s.get("extra") or ""),
        })
    milestones = []
    for m in data.get("milestones") or []:
        if isinstance(m, dict) and m.get("stage"):
            milestones.append({"stage": str(m["stage"]),
                               "end": str(m.get("end") or ""),
                               "grad": str(m.get("grad") or "完成本阶段全部任务")})
    return {"milestones": milestones, "days": days}

def gen_custom_plan(goal, start, end, minutes, background, cfg):
    """调用 LLM 生成逐日计划；解析失败自动重试一次。"""
    ndays = (end - start).days + 1
    msgs = [{"role": "system", "content": "你是专业的学习计划制定师，只输出 JSON。"},
            {"role": "user", "content": PLAN_PROMPT.format(
                goal=goal, start=start.isoformat(), end=end.isoformat(),
                ndays=ndays, minutes=minutes, bg=background)}]
    last = None
    for _ in range(2):
        try:
            return normalize_plan(extract_json(llm_chat(cfg, msgs)), start, end)
        except Exception as e:
            last = e
            msgs.append({"role": "user", "content":
                         f"上次输出无法使用（{e}）。请严格按要求的 JSON 结构重新输出，days 长度 {ndays}。"})
    raise RuntimeError(f"LLM 生成计划失败：{last}")

# ---------- 状态 ----------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {"last_date": None, "q_index": 0, "round": 1, "history": {}, "streak": 0}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ---------- 工具 ----------
def stage_of(d):
    iso = d.isoformat()
    last_grad = "—"
    for a, b, station, main, hand, ai, grad in STAGES:
        if grad and grad != "同上":
            last_grad = grad
        if a <= iso <= b:
            return (station, main, hand, ai, last_grad if grad == "同上" else grad)
    return ("计划外", "按 v3 计划复盘/休整（或提前开始下一阶段准备）", "复习本周手写代码", "—", "—")

def plan_of(d):
    """今日任务来源：自定义计划（plan_config.json mode=custom）或内置 STAGES。"""
    iso = d.isoformat()
    cfg = load_plan_cfg()
    if cfg.get("mode") == "custom" and cfg.get("days"):
        days = cfg["days"]
        goal = cfg.get("goal", "")
        for idx, x in enumerate(days):
            if x["date"] == iso:
                grad = ""
                for m in cfg.get("milestones") or []:
                    if m.get("stage") == x["stage"] and m.get("grad"):
                        grad = m["grad"]
                return {"station": x["stage"], "main": x["main"], "hand": x["hand"],
                        "ai": x["ai"], "grad": grad or f"完成「{x['stage']}」阶段全部任务",
                        "extra": x.get("extra", ""), "goal": goal,
                        "day_no": idx + 1, "day_total": len(days)}
        if iso < days[0]["date"]:
            main = f"自定义计划将于 {days[0]['date']} 开始：{goal}（今天自由准备）"
        else:
            main = "本计划已到期。用 --new-goal 制定新计划，或 --builtin 切回内置计划"
        return {"station": "自定义计划", "main": main, "hand": "—", "ai": "—", "grad": "—",
                "extra": "", "goal": goal, "day_no": 0, "day_total": len(days)}
    station, main, hand, ai, grad = stage_of(d)
    return {"station": station, "main": main, "hand": hand, "ai": ai, "grad": grad,
            "extra": SQL_DAYS.get(iso, ""), "goal": "", "day_no": 0, "day_total": 0}

HELPER_LL = '''
# ---- 链表工具（本地测试用，提交 LeetCode 时删掉这部分）----
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val; self.next = next
    def __repr__(self): return f"ListNode({self.val})"

def build_list(vals):
    dummy = ListNode(); cur = dummy
    for v in vals: cur.next = ListNode(v); cur = cur.next
    return dummy.next

def to_list(head):
    out = []
    while head: out.append(head.val); head = head.next
    return out
'''

HELPER_TREE = '''
# ---- 二叉树工具（层序数组建树，提交 LeetCode 时删掉这部分）----
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val; self.left = left; self.right = right

def build_tree(vals):
    if not vals: return None
    root = TreeNode(vals[0]); q = [root]; i = 1
    while q and i < len(vals):
        node = q.pop(0)
        if i < len(vals) and vals[i] is not None:
            node.left = TreeNode(vals[i]); q.append(node.left)
        i += 1
        if i < len(vals) and vals[i] is not None:
            node.right = TreeNode(vals[i]); q.append(node.right)
        i += 1
    return root

def level_order(root):
    if not root: return []
    out, q = [], [root]
    while q:
        node = q.pop(0); out.append(node.val if node else None)
        if node: q += [node.left, node.right]
    while out and out[-1] is None: out.pop()
    return out
'''

DESIGN_TEST_COMMENT = """# ---- 测试建议（设计题）----
# 按题目示例的操作序列逐行调用，例如 MinStack：
# s = MinStack(); s.push(-2); s.push(0); s.push(-3)
# assert s.getMin() == -3
# s.pop(); assert s.top() == 0; assert s.getMin() == -2
# 本地跑通全部断言后再 commit。
"""

def render_case(case, tags):
    """生成一个 test() 函数体；case=(args_list, expected)"""
    args, expected = case
    if "ll" in tags:
        calls = []
        list_args = []
        for i, a in enumerate(args):
            list_args.append(f"a{i}")
        return list_args, expected
    return None

def build_practice(qi, qmeta, d, review=False, round_no=1):
    num, name, slug, diff, tags, sig, hint, cases = qmeta
    # 兼容旧版 _v_r{qi+1}.py 文件名，新生成的复习文件统一用 _review.py
    legacy_fname = f"{d.strftime('%m-%d')}_lc{num}_{slug}_v_r{qi+1}.py"
    if review and os.path.exists(os.path.join(PRAC_DIR, legacy_fname)):
        fname = legacy_fname
    else:
        fname = f"{d.strftime('%m-%d')}_lc{num}_{slug}{'_review' if review else ''}.py"
    path = os.path.join(PRAC_DIR, fname)
    if os.path.exists(path):
        return path, fname  # 幂等：不覆盖已生成（尤其已手写）的文件

    if review:
        round_tag = "周日复习重做（关掉提示凭记忆手写）" if d.weekday() == 6 else f"第 {round_no} 轮复习"
    else:
        round_tag = f"第 {qi+1}/{len(LC_PLAN)} 关 · 新题"
    L = []
    L.append('"""')
    L.append(f"LeetCode {num}. {name}  [{diff}]  —  {round_tag}")
    L.append(f"链接: https://leetcode.cn/problems/{slug}/")
    L.append(f"日期: {d.isoformat()} {WEEKDAYS[d.weekday()]}  |  目标用时: 20 分钟本地跑通")
    L.append(f"思路提示（卡住 10 分钟再看）: {hint}")
    L.append('"""')
    L.append("from typing import List, Optional")
    if "ll" in tags:
        L.append(HELPER_LL)
    if "tree" in tags:
        L.append(HELPER_TREE)
    L.append("")
    if "design" in tags:
        L.append(sig)
        L.append('    """TODO: 在此类中实现题目要求的各个方法（先自己写，跑通后再让 AI review）"""')
        L.append("    pass")
    else:
        L.append("class Solution:")
        L.append("    " + sig)
        L.append('        """TODO: 你的手写代码从这里开始 —— 先自己写，跑通后再让 AI review"""')
        L.append("        pass")
    L.append("")
    L.append("# ---- 本地测试：看到 AC! 再 commit ----")
    if cases is None:
        L.append(DESIGN_TEST_COMMENT.rstrip("\n"))
    elif "design" in tags:
        L.append("def test():")
        for line in cases:
            L.append("    " + line)
        L.append('    print("AC!")')
        L.append("")
        L.append('if __name__ == "__main__":')
        L.append("    test()")
    else:
        L.append("def test():")
        L.append("    sol = Solution()")
        test_idx = 0
        for args, expected in cases:
            test_idx += 1
            method = sig.split('(')[0].split()[-1]
            if "ll" in tags:
                if num == 141:
                    vals, pos = args
                    L.append(f"    h{test_idx} = build_list({vals!r})")
                    if pos >= 0:
                        L.append(f"    cur = h{test_idx}; cyc = None")
                        L.append(f"    for idx in range({len(vals)}):")
                        L.append(f"        if idx == {pos}: cyc = cur")
                        L.append(f"        if cur.next: cur = cur.next")
                        L.append(f"    if cur: cur.next = cyc")
                    L.append(f"    assert sol.{method}(h{test_idx}) == {expected!r}, 'case {test_idx}'")
                else:
                    for i, a in enumerate(args):
                        L.append(f"    a{i} = build_list({a!r})")
                    L.append(f"    got = to_list(sol.{method}({', '.join(f'a{i}' for i in range(len(args)))}))")
                    L.append(f"    assert got == {expected!r}, (got, {expected!r})")
            elif "tree" in tags:
                L.append(f"    t0 = build_tree({args[0]!r})")
                call_args = ["t0"] + [repr(a) for a in args[1:]]
                if num in (144, 94, 102):
                    L.append(f"    assert sol.{method}(t0) == {expected!r}")
                elif num == 226:
                    L.append(f"    assert level_order(sol.{method}(t0)) == {expected!r}")
                elif num == 236:
                    L.append(f"    got = sol.{method}(t0, t0.left, t0.right)")
                    L.append(f"    assert got is not None and got.val == {expected!r}")
                else:
                    L.append(f"    assert sol.{method}({', '.join(call_args)}) == {expected!r}")
            else:
                if "inplace" in tags:
                    L.append(f"    nums = {args[0]!r}")
                    L.append(f"    sol.{method}(nums)")
                    L.append(f"    assert nums == {expected!r}, 'case {test_idx}'")
                elif "lol_eq" in tags:
                    rendered = [repr(a) for a in args]
                    L.append(f"    got = sol.{method}({', '.join(rendered)})")
                    L.append(f"    assert sorted(sorted(x) for x in got) == sorted(sorted(x) for x in {expected!r}), (got, {expected!r})")
                elif "set_eq" in tags:
                    rendered = [repr(a) for a in args]
                    L.append(f"    got = sol.{method}({', '.join(rendered)})")
                    L.append(f"    assert sorted(got) == sorted({expected!r}), (got, {expected!r})")
                else:
                    rendered = [repr(a) for a in args]
                    L.append(f"    assert sol.{method}({', '.join(rendered)}) == {expected!r}, 'case {test_idx}'")
        L.append('    print("AC!")')
        L.append("")
        L.append('if __name__ == "__main__":')
        L.append("    test()")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path, fname

def _flagged_review_qnums():
    """扫描 learning-notes 与 Obsidian 两个笔记库，返回标记了『需二刷 / 不会 / 半懂 / 否』的题号集合。"""
    flagged = set()
    for store in _note_stores():
        root = store["root"]
        readme_path = os.path.join(root, "README.md")
        if os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        if "需二刷" in line and line.strip().startswith("|"):
                            m = re.match(r"^\|\s*0*(\d+)\s*\|", line.strip())
                            if m:
                                flagged.add(int(m.group(1)))
            except Exception:
                pass
        notes_sub = os.path.join(root, "notes")
        if not os.path.isdir(notes_sub):
            continue
        for fn in os.listdir(notes_sub):
            if not fn.endswith(".md"):
                continue
            m = re.match(r"^0*(\d+)-", fn)
            if not m:
                continue
            qnum = int(m.group(1))
            try:
                with open(os.path.join(notes_sub, fn), "r", encoding="utf-8-sig") as f:
                    txt = f.read()
                # 兼容 Obsidian YAML（status: 需二刷）与纯 Markdown（- 状态：需二刷）
                if re.search(r"^(?:status:\s*需二刷|- 状态：\s*需二刷)", txt, flags=re.MULTILINE):
                    flagged.add(qnum)
                if re.search(r"^- 是否能不看答案写出来：\s*(否|不会|半懂|不能)", txt, flags=re.MULTILINE):
                    flagged.add(qnum)
            except Exception:
                pass
    return flagged


def pick_question(d, state):
    """返回 (qmeta, review_bool, qi)。
    周日智能抽题优先级：
      1) 刷题笔记中标记为『需二刷 / 不会 / 半懂』的已学题（优先攻克薄弱项）
      2) 本周（近6天）新做且未复习过的 Medium/Hard 题
      3) 本周新做且未复习过的其他题
      4) 7~21天前做过且从未周日复习过的 Medium/Hard 题（艾宾浩斯间隔回顾）
    """
    is_sunday = d.weekday() == 6
    if is_sunday and state["history"]:
        today_iso = d.isoformat()
        week_ago = (d - timedelta(days=6)).isoformat()
        three_weeks_ago = (d - timedelta(days=21)).isoformat()
        two_weeks_ago = (d - timedelta(days=14)).isoformat()

        recent_reviewed = {
            h["qi"] for iso, h in state["history"].items()
            if iso < today_iso and iso >= two_weeks_ago and h.get("review")
        }
        all_reviewed = {
            h["qi"] for iso, h in state["history"].items()
            if iso < today_iso and h.get("review")
        }
        done_all = [
            (iso, h["qi"]) for iso, h in sorted(state["history"].items())
            if iso < today_iso and not h.get("review") and not h.get("py_stage")
        ]

        # 1) 优先抽标记了「需二刷 / 不会 / 半懂」的题
        flagged_nums = _flagged_review_qnums()
        if flagged_nums:
            flagged_qis = [
                qi for _, qi in done_all
                if qmeta_of(qi)[0] in flagged_nums and qi not in recent_reviewed
            ]
            if flagged_qis:
                qi = flagged_qis[0]
                return qmeta_of(qi), True, qi

        # 2) 本周新题：优先选尚未复习过的 Medium / Hard
        week_done = [
            qi for iso, qi in done_all
            if iso >= week_ago and qi not in recent_reviewed
        ]
        if week_done:
            mh = [qi for qi in week_done if qmeta_of(qi)[3] in ("Hard", "Medium") and qi not in all_reviewed]
            if mh:
                qi = random.choice(mh)
                return qmeta_of(qi), True, qi
            unrev = [qi for qi in week_done if qi not in all_reviewed]
            qi = random.choice(unrev if unrev else week_done)
            return qmeta_of(qi), True, qi

        # 3) 艾宾浩斯 7~21 天间隔复习
        older_done = [
            qi for iso, qi in done_all
            if three_weeks_ago <= iso < week_ago and qi not in all_reviewed
        ]
        if older_done:
            mh_old = [qi for qi in older_done if qmeta_of(qi)[3] in ("Hard", "Medium")]
            qi = random.choice(mh_old if mh_old else older_done)
            return qmeta_of(qi), True, qi

    qi = state["q_index"]
    if qi >= len(LC_PLAN):
        qi = qi % len(LC_PLAN)  # 二刷
        review = True
    else:
        review = False
    return qmeta_of(qi), review, qi

def build_md(d, plan, qmeta, qi, review, qfname, state, carry=None, lc=None, weekend=None, py_camp=None, py_all=False, note_path=None):
    iso = d.isoformat()
    wd = WEEKDAYS[d.weekday()]
    num, qname, slug, diff, tags, sig, hint, cases = qmeta
    round_no = state["q_index"] // len(LC_PLAN) + 1
    qtag = "复习重做" if review else f"第 {qi+1}/{len(LC_PLAN)} 关（第 {round_no} 轮）"
    is_sunday = d.weekday() == 6
    is_weekend = d.weekday() >= 5
    sql_line = plan.get("extra") or None
    station, main_task = plan["station"], plan["main"]
    hand, ai_help, grad = plan["hand"], plan["ai"], plan["grad"]
    py_on = bool(py_camp and not py_camp["all_done"]
                 and d.isoformat() <= state.get("py_camp_end", "9999-12-31"))
    lc_on = bool(LC_FOCUS and lc and not py_on)
    sec = 3 if (lc_on or py_on) else 2
    prac_full = os.path.join(PRAC_DIR, qfname) if qfname else ""
    ielts_file = latest_ielts_file()
    L = []
    L.append(f"# 今日学习计划 · {iso} {wd}")
    L.append("")
    if py_all:
        track = f"🐍 Python 基础冲刺（9 关今天全通）"
    elif py_on:
        track = f"🐍 Python 基础 第 {py_camp['index']+1}/{py_camp['total']} 关"
    else:
        track = qtag
    L.append(f"**当前技能站：{station}** ｜ 连续打卡：**{state['streak']} 天** ｜ {track}")
    if plan.get("goal"):
        L.append(f" ｜ 🎯 **{plan['goal']}**（Day {plan['day_no']}/{plan['day_total']}）")
    L.append("")
    if carry:
        L.append("> ⚠️ 有未确认的遗留计划：" + "、".join(carry) +
                 "。完成后双击桌面「✅完成今日计划」一并归档；未确认前不推进新题。")
        L.append("")
    L.append("## 1. 主任务（45-90 分钟，当天必须有 commit；课程/考试周优先保 GPA）")
    L.append(f"- **本周主线**：{main_task}")
    if not plan.get("goal"):
        wf_title, wf_desc = WEEKDAY_FOCUS[d.weekday()]
        L.append(f"- 📅 **今日推进重点（{wf_title}）**：{wf_desc}")
    L.append(f"- ✍️ **必须手写**：{hand}")
    L.append(f"- 🤖 **可交给 AI**：{ai_help}")
    L.append(f"- 🎯 **本站毕业标准**：{grad}")
    L.append("")
    if py_on:
        idx = py_camp["index"]
        if py_all:
            L.append("## 2. 🐍 Python 基础关（冲刺日 · 9 关今天全通，约 3-4 小时）")
            L.append(f"**当前进度 {py_camp['done']}/{py_camp['total']} 关已全过** ｜ 建议每关 20 分钟，"
                     f"卡住超过 10 分钟先看文件顶部提示，别死磕 ｜ **明日起恢复 LeetCode 闯关**")
            L.append("")
            for i, s in enumerate(py_camp["stages"]):
                if s["total"] and s["passed"] == s["total"]:
                    mark = f"✅ 已通过 {s['passed']}/{s['total']}"
                elif s.get("error"):
                    mark = "⚠️ 文件有语法错误，先修好"
                else:
                    mark = f"{s['passed']}/{s['total']}"
                    if s["pending"]:
                        mark += f" ｜ 待过：{'、'.join(s['pending'])}"
                L.append(f"- [ ] **第 {i+1} 关 {s['title']}** — `{s['name']}.py` — {mark}")
            L.append(f"- [ ] **收官自检**：在 `{PY_CAMP_DIR}` 运行 `python check.py`，9 关全 `[通过]` 才算通关")
            L.append("")
        else:
            L.append("## 2. 🐍 Python 基础关（20-30 分钟 · 暂替算法闯关）")
            L.append(f"**第 {idx+1}/{py_camp['total']} 关：{py_camp['title']}** ｜ 已完成 {py_camp['done']} 关 ｜ 本关 {py_camp['passed']}/{py_camp['count']} 过")
            L.append(f"- [ ] **打开文件**：`{PY_CAMP_DIR}\\{py_camp['name']}.py`（VS Code，只改中间的函数体）")
            if py_camp.get("error"):
                L.append("- ⚠️ 本关文件当前有语法错误（或改动还没保存），先修好能正常运行再自检")
            elif py_camp["pending"]:
                L.append(f"- [ ] **今天要过的函数**：{'、'.join(py_camp['pending'])}")
            else:
                L.append("- [ ] **今天重写一遍本关**：把已通过的实现删掉，凭记忆重写（防遗忘）")
            L.append(f"- [ ] **自检**：终端 `python {py_camp['name']}.py`，全部 [通过] 才算过关")
            L.append(f"- 进度说明：9 关走完自动恢复 LeetCode 闯关（当前 {py_camp['done']}/{py_camp['total']}）")
            L.append("")
    elif lc_on:
        topic, _, extras = lc
        main_k, rel_k = _knowledge_links(num, topic)
        L.append("## 2. 算法闯关（30-40 分钟）")
        L.append(f"**今日主题：{topic}** ｜ {qtag}")
        if main_k:
            kn = f"**今日知识点：[[{main_k}]]**"
            if rel_k:
                kn += " ｜ 关联：" + " · ".join(f"[[{r}]]" for r in rel_k)
            L.append(kn)
            L.append(f"      做题前先扫一眼 [[{main_k}]] 的「识别信号」和「常见坑」，做完回来补一句自己的话。")
        L.append(f"- [ ] **主练（手写跑通 AC）**：LeetCode {num}. {qname} [{diff}]")
        L.append(f"      练习文件已生成：`{prac_full}` —— 在 VS Code 打开手写，本地跑通看到 AC!（禁止纸上默写）")
        if note_path:
            L.append(f"      刷题笔记已就绪：`{note_path}` —— 记录第一版思路/卡点/一句话总结（确认归档时自动标 AC 并同步代码）")
        L.append(f"      题目链接：https://leetcode.cn/problems/{slug}/")
        ex = "　｜　".join(f"[LC {n}. {t} [{df}]](https://leetcode.cn/problems/{s}/)" for n, t, s, df in extras)
        L.append(f"- [ ] **举一反三（网页直接做，卡 10 分钟看题解再默写）**：{ex}")
        pairs = BONUS.get(topic) or []
        k = sum(1 for e in LC_PLAN[: qi % len(LC_PLAN)] if e[0] == topic)
        bonus = pairs[k % len(pairs)] if pairs else []
        if bonus:
            bx = "　｜　".join(f"[LC {n}. {t} [{df}]](https://leetcode.cn/problems/{s}/)" for n, t, s, df in bonus)
            L.append(f"- [ ]（选做）**加练**：{bx}")
        L.append("")
    L.append(f"## {sec}. 碎片任务")
    if not lc_on and not py_on:
        L.append(f"- [ ] **算法手写 20min**：LeetCode {num}. {qname} [{diff}]（{qtag}）")
        L.append(f"      练习文件已生成：`{prac_full}` —— 在 VS Code 打开手写，本地跑通看到 AC!（禁止纸上默写）")
        if note_path:
            L.append(f"      刷题笔记已就绪：`{note_path}` —— 记录思路/卡点/一句话总结（确认时自动标 AC）")
        L.append(f"      题目链接：https://leetcode.cn/problems/{slug}/")
    if sql_line:
        L.append(f"- [ ] **{sql_line}**")
    L.append(f"- [ ] **英语 30min（IELTS + 英文技术阅读）**：雅思同义替换/听读词汇整理（记录至 `{ielts_file}`）+ 1 篇英文技术文档/论文摘要")
    L.append("")
    next_no = sec + 1
    if is_weekend and weekend:
        w_title, w_body, w_deliver = weekend
        L.append(f"## {next_no}. 🎨 周末产出日（可视化产出 · 用上学过的知识）")
        L.append(f"- **{w_title}**")
        L.append(f"- 📚 **融会贯通**：{w_body}")
        L.append(f"- 🏆 {w_deliver}")
        L.append("- 硬性规则：图里的数字必须是本周手写代码真实跑出来的，不许编造/手绘数据")
        L.append("")
        next_no += 1
    if is_sunday:
        L.append(f"## {next_no}. 周日特殊任务")
        L.append("- [ ] **重写日 30min**：把本周 AI 帮写最多的一段核心代码删掉，凭理解重写一遍")
        L.append("- [ ] **Demo Day 30min**：把上面周末产出日的作品录屏/GIF，写进 learning-notes/progress.md 产出墙")
        L.append("- [ ] **概念卡 10min**：复习本周+上周的概念卡")
        L.append("")
        next_no += 1
    L.append(f"## {next_no}. 手写 vs AI 红线")
    L.append("- 顺序永远是 **先自己写 → 跑通 → 再让 AI review**，不要反过来")
    L.append("- 不粘贴没读懂的长代码；核心代码（损失/训练循环/检索/评估/路由）必须自己写")
    L.append("")
    next_no += 1

    # 知识点回顾：只在该题真的挂了知识点时出现
    k_topic = lc[0] if lc_on else ""
    main_k, rel_k = _knowledge_links(num, k_topic)
    if main_k:
        L.append(f"## {next_no}. 知识点回顾（做完题花 3 分钟）")
        L.append(f"1. 打开 [[{main_k}]]，对照「识别信号」写一句：**这题为什么属于它**。")
        L.append(f"2. 对照「常见坑」自查：今天有没有踩到其中一条？踩到了就把当时的现象补进那一条下面。")
        if rel_k:
            L.append(f"3. 顺带扫一眼关联知识点：{' · '.join(f'[[{r}]]' for r in rel_k)}，确认没有混淆。")
        L.append(f"4. 如果发现自己用了别的套路，说明映射要调：改 `daily/knowledge_map.py` 里 "
                 f"`QUESTION_KNOWLEDGE[{num}]`，下次生成自动生效。")
        L.append("")
        next_no += 1

    L.append(f"## {next_no}. 完成后（确认归档）")
    L.append("0. **一键开启工作台**：双击桌面「🚀开始今日学习」（或运行 `python gen_daily.py --open`），自动用 VS Code 打开本计划 + 练习 `.py` + 刷题笔记并打开网页题目")
    if py_all:
        L.append("1. `python check.py` 显示 9 关全 `[通过]`，主任务当天有 commit")
    elif py_on:
        L.append(f"1. 第 {py_camp['index']+1} 关全部 `[通过]`，主任务当天有 commit")
    else:
        L.append("1. 练习文件本地跑通看到 `AC!`，主任务当天有 commit")
    L.append("2. **双击桌面「✅完成今日计划」**：自动验证本地 `AC!` → 勾选完成项并汇总今日各仓库 Git Commits → 归档至 `dailyPlan` 仓库并推进新题")
    if py_all:
        L.append("3. 没做完也不影响明天：**明天自动回到 LeetCode 闯关**（今天能做几关就几关，进度已保存在代码里）")
    else:
        L.append("3. 没完成就不点确认：文件保留，明天继续攻克同一题（最小方案：练习文件写 1 行也是进步）")
    L.append("")
    L.append(f"---\n_由 gen_daily.py 于 {datetime.now().strftime('%Y-%m-%d %H:%M')} 自动生成_")
    return "\n".join(L)

def main(force=False):
    today = date.today()
    state = load_state()
    iso = today.isoformat()
    args = sys.argv[1:]
    force_regen = force or any(f in args for f in ("--regen", "--py-all", "--new-goal", "--import-plan", "--builtin"))

    # 幂等：同一天重复运行不推进（连续打卡与题号由 --confirm 确认时结算）
    state["last_date"] = iso

    # 今天已确认归档：重启 / 重复触发不得再生成（防止"已完成的任务复活"）
    # 例外：--py-all 冲刺日需要主动覆盖今天（已归档的日子也拦）；--regen 只能刷新未确认的当天，不会复活已归档日
    if state["history"].get(iso, {}).get("status") == "done" and "--py-all" not in args:
        print("TODAY_DONE")
        write_run_result({"result": "today_done", "date": iso})
        return

    plan_path = os.path.join(REPO_PLANS, f"{iso}.md")
    dp = os.path.join(desktop_dir(), f"学习计划_{iso}.md")

    # 同一天已生成过且未加 --regen：保持已有计划文件时间戳不变（避免一天内重复触发产生二次 git commit 和二次弹窗）
    if (not force_regen
            and iso in state["history"]
            and state["history"][iso].get("status") == "pending"
            and os.path.exists(plan_path)):
        h = state["history"][iso]
        qi, review = h["qi"], h["review"]
        qmeta = qmeta_of(qi)
        round_no = state["q_index"] // len(LC_PLAN) + 1
        if not h.get("py_stage"):
            build_practice(qi, qmeta, today, review=review, round_no=round_no)
        if not os.path.exists(dp):
            try:
                with open(plan_path, "r", encoding="utf-8") as rf, open(dp, "w", encoding="utf-8") as wf:
                    wf.write(rf.read())
            except Exception as e:
                print("desktop restore failed:", e, file=sys.stderr)
        print(f"ALREADY_EXISTS:{iso}")
        write_run_result({"result": "already_exists", "date": iso, "streak": state["streak"]})
        return

    def pending_dates(today_iso):
        return sorted(d for d, h in state["history"].items()
                      if h.get("status", "pending") == "pending" and d < today_iso)

    carry = []
    if iso in state["history"]:
        h = state["history"][iso]
        qi, review = h["qi"], h["review"]
        qmeta = qmeta_of(qi)
        carry = pending_dates(iso)
    else:
        pend = pending_dates(iso)
        if pend:
            # 有未确认的遗留计划：不推进新题，继续攻克最早未完成的那题
            h0 = state["history"][pend[0]]
            qi, review = h0["qi"], h0["review"]
            qmeta = qmeta_of(qi)
            carry = pend
        else:
            qmeta, review, qi = pick_question(today, state)
        state["history"][iso] = {"qi": qi, "review": review,
                                 "station": stage_of(today)[0], "status": "pending"}

    py_camp = py_camp_status()
    py_all = ("--py-all" in args) and bool(py_camp)
    if py_all:
        state["py_camp_end"] = iso   # 冲刺日：从明天起强制恢复 LeetCode，不再看剩余关卡
    py_on = bool(py_camp and not py_camp["all_done"]
                 and iso <= state.get("py_camp_end", "9999-12-31"))
    # 标记该日走的是 Python 基础关：确认归档时不推进 LeetCode 题号（否则会空跳题）
    state["history"][iso]["py_stage"] = py_camp["name"] if py_on else ""
    round_no = state["q_index"] // len(LC_PLAN) + 1
    lc = lc_entry(qi)
    if py_on:
        path, qfname, note_path = None, "", None
    else:
        path, qfname = build_practice(qi, qmeta, today, review=review, round_no=round_no)
        note_path = ensure_lc_note(qmeta, lc[0], iso, path)
    plan = plan_of(today)
    weekend = weekend_project(today, plan["station"]) if today.weekday() >= 5 else None
    state["history"][iso]["station"] = plan["station"]
    md = build_md(today, plan, qmeta, qi, review, qfname, state, carry=carry, lc=lc, weekend=weekend, py_camp=py_camp, py_all=py_all, note_path=note_path)

    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(md)

    desktop_ok = False
    try:
        with open(dp, "w", encoding="utf-8") as f:
            f.write(md)
        desktop_ok = True
    except Exception as e:
        print("desktop write failed:", e, file=sys.stderr)

    # Obsidian 库镜像：计划 md（带 YAML frontmatter）+ 手写练习骨架 + 脚本/状态副本
    obs_plan = mirror_plan_to_obsidian(iso, md, plan=plan, qmeta=qmeta, review=review,
                                       topic=(lc[0] if lc else None))
    if qfname:
        mirror_practice_to_obsidian(qfname)

    save_state(state)
    sync_obsidian_scripts()

    # toast 摘要（ps1 读取，避免中文硬编码在 ps1）
    summary = os.path.join(BASE, "last_summary.txt")
    qnum, qname = qmeta[0], qmeta[1]
    with open(summary, "w", encoding="utf-8") as f:
        f.write(f"今日计划已生成 | 连续 {state['streak']} 天\n")
        if py_on:
            if py_all:
                f.write(f"{plan['station']}：Python 基础冲刺，9 关今天全通（约 3-4h）\n")
            else:
                f.write(f"{plan['station']}：Python 基础第 {py_camp['index']+1}/{py_camp['total']} 关"
                        f"（{py_camp['title']}）\n")
        else:
            f.write(f"{plan['station']}：{qnum}.{qname}（{'复习' if review else '新题'}）已就位\n")

    print(f"[OK] {iso} {WEEKDAYS[today.weekday()]} streak={state['streak']}")
    print(f"plan: {plan_path}")
    print(f"practice: {path}")
    print(f"desktop: {desktop_ok}")
    if obs_plan:
        print(f"obsidian: {obs_plan}")
    write_run_result({"result": "ok", "date": iso,
                      "station": plan["station"], "q": f"{qnum}.{qname}",
                      "review": review, "streak": state["streak"],
                      "py_stage": py_camp["name"] if py_on else "",
                      "obsidian": obs_plan or ""})

def _obsidian_plan_frontmatter(iso, plan, qmeta, review, topic=None):
    num, qname = qmeta[0], qmeta[1]
    main_k, rel_k = _knowledge_links(num, topic or plan.get("station", ""))
    lines = [
        "---",
        f'title: "学习计划 {iso}"',
        "tags:",
        "  - 学习计划",
        "  - 每日计划",
        f"date: {iso}",
        "status: 进行中",
        f"skill_station: {plan.get('station', '')}",
        f"leetcode: {num}. {qname}",
        f"review: {'是' if review else '否'}",
    ]
    if main_k:
        lines.append("knowledge:")
        lines.append(f'  - "{main_k}"')
        if rel_k:
            lines.append("knowledge_related:")
            for r in rel_k:
                lines.append(f'  - "{r}"')
    lines.append("---")
    lines.append("")
    return lines


def _parse_plan_meta(md):
    """从计划正文里解析技能站 / 算法题 / 是否复习，供 Obsidian 归档副本补 frontmatter。"""
    meta = {}
    m = re.search(r"\*\*当前技能站：([^*]+)\*\*", md)
    if m:
        meta["station"] = m.group(1).strip()
    m = re.search(r"LeetCode (\d+)\. ([^\[（]+?)\s*\[", md)
    if m:
        meta["leetcode"] = f"{m.group(1)}. {m.group(2).strip()}"
    meta["review"] = "是" if "复习重做" in md else "否"
    # 知识点双链：正文里「今日知识点：[[xxx]] ｜ 关联：[[a]] · [[b]]」
    meta["knowledge"] = re.findall(r"\[\[([^\]|#]+)\]\]", md)
    # 只保留真正的知识点（排除题单、索引这类导航链接）
    skip = {"LeetCode 刷题总览", "知识点总览", "学习看板", "知识库总览", "学习路线", "全部笔记索引"}
    meta["knowledge"] = [k.strip() for k in meta["knowledge"] if k.strip() not in skip]
    return meta


def mirror_plan_to_obsidian(iso, md, plan=None, qmeta=None, review=False, archived=False, topic=None):
    """把每日计划同步一份进 Obsidian 库（plans/ 进行中，done/YYYY-MM/ 已归档）。"""
    if not OBS_ENABLED:
        return None
    target_dir = os.path.join(OBS_DONE, iso[:7]) if archived else OBS_PLANS
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{iso}.md")
    if plan is not None and qmeta is not None:
        header = _obsidian_plan_frontmatter(iso, plan, qmeta, review, topic=topic)
    else:
        meta = _parse_plan_meta(md)
        header = [
            "---",
            f'title: "学习计划 {iso}"',
            "tags:",
            "  - 学习计划",
            "  - 每日计划",
            f"date: {iso}",
            f"status: {'完成' if archived else '进行中'}",
            f"skill_station: {meta.get('station', '')}",
            f"leetcode: {meta.get('leetcode', '')}",
            f"review: {meta.get('review', '否')}",
        ]
        if meta.get("knowledge"):
            header.append("knowledge:")
            for k in meta["knowledge"]:
                header.append(f'  - "{k}"')
        header += ["---", ""]
    text = "\n".join(header) + md
    try:
        _write_lf(target, text)
    except Exception as e:
        print("obsidian plan mirror failed:", e, file=sys.stderr)
        return None
    return target


def mirror_practice_to_obsidian(fname):
    """把当日手写练习骨架同步一份进 Obsidian 库的 practice/。"""
    if not OBS_ENABLED or not fname:
        return None
    src = os.path.join(PRAC_DIR, fname)
    if not os.path.exists(src):
        return None
    dst = os.path.join(OBS_PRAC, fname)
    try:
        if os.path.abspath(src) != os.path.abspath(dst):
            # .py 也按字节复制：保留原文件换行风格，避免每次同步都制造整片假 diff
            shutil.copyfile(src, dst)
    except Exception as e:
        print("obsidian practice mirror failed:", e, file=sys.stderr)
        return None
    return dst


def sync_obsidian_scripts():
    """把本目录的脚本与运行状态同步到 Obsidian 库内的 daily/ 副本（副本只作备份，不参与运行）。"""
    if not OBS_ENABLED:
        return []
    if os.path.abspath(BASE) == os.path.abspath(OBS_DAILY):
        return []
    copied = []
    names = ("gen_daily.py", "knowledge_map.py", "run_daily.ps1", "done.ps1", "commit_evening.ps1",
             "install_task.ps1", "open_daily.ps1", "remind_evening.ps1", "sync_all.ps1", ".gitignore",
             "state.json", "plan_config.json", "last_summary.txt", "last_run.json")
    for name in names:
        src = os.path.join(BASE, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(OBS_DAILY, name)
        try:
            # 一律按字节原样复制：.ps1 的 UTF-8 BOM、.gitignore 的换行符都必须保持，
            # 转码会让 PowerShell 解析失败或让 git 把整份文件当成改动
            shutil.copyfile(src, dst)
            copied.append(name)
        except Exception as e:
            print("obsidian script sync failed:", name, e, file=sys.stderr)
    return copied


def _collect_git_commits(iso):
    """扫描 WORKSPACE_ROOT 下各独立 Git 仓库（不含 dailyPlan 归档仓库自身）在指定日期 iso 产生的提交。"""
    commits = []
    if not os.path.isdir(WORKSPACE_ROOT):
        return commits
    for name in sorted(os.listdir(WORKSPACE_ROOT)):
        if name == "dailyPlan":
            continue
        repo_dir = os.path.join(WORKSPACE_ROOT, name)
        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            continue
        try:
            proc = _run_quiet(
                [
                    "git", "-C", repo_dir, "log",
                    f"--since={iso} 00:00:00",
                    f"--until={iso} 23:59:59",
                    "--pretty=format:%h %s",
                ],
                timeout=5
            )
            if proc.returncode == 0 and proc.stdout.strip():
                for line in proc.stdout.strip().splitlines():
                    commits.append((name, line.strip()))
        except Exception:
            pass
    return commits


def _practice_ac_status_for_date(iso, h_entry):
    """检测某日练习文件是否已本地跑通 AC!，返回简短状态文案。"""
    if h_entry.get("py_stage"):
        return f"🐍 Python 基础关（{h_entry['py_stage']}）已确认"
    qmeta = qmeta_of(h_entry["qi"])
    num, qname, slug = qmeta[0], qmeta[1], qmeta[2]
    prac_path, fname = _find_practice_file(iso, num, slug)
    if not os.path.exists(prac_path) or not _extract_user_code(prac_path):
        return f"☑️ 已确认完成（`{fname}` 为网页端完成或未写本地代码）"
    try:
        proc = _run_quiet(
            [sys.executable, "-X", "utf8", prac_path],
            timeout=8
        )
        if proc.returncode == 0 and "AC!" in (proc.stdout or ""):
            return f"✅ 本地代码已跑通 `AC!`（`{fname}`）"
        return f"☑️ 已确认完成（`{fname}` 本地代码已编写）"
    except Exception:
        return f"☑️ 已确认完成（`{fname}`）"


def _finalize_archived_md(iso, dst_path, streak, h_entry):
    """归档时自动将任务复选框打勾 [x]，并追加『📊 今日完成结算记录』（含当日各仓库 Git 提交汇总）。"""
    if not os.path.exists(dst_path):
        return
    try:
        with open(dst_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        # 自动勾选所有必做任务复选框（保留「（选做）」原样）
        out_lines = []
        for line in content.splitlines():
            if line.startswith("- [ ] ") and not line.startswith("- [ ]（选做）"):
                out_lines.append("- [x] " + line[6:])
            else:
                out_lines.append(line)
        updated = "\n".join(out_lines)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        prac_status = _practice_ac_status_for_date(iso, h_entry)
        commits = _collect_git_commits(iso)
        rec = [
            "## 📊 今日完成结算记录",
            f"- **确认归档时间**：{now_str}",
            f"- **连续打卡**：{streak} 天",
            f"- **练习自检状态**：{prac_status}",
        ]
        if commits:
            rec.append(f"- **当日 Git 提交汇总（共 {len(commits)} 条）**：")
            for repo_name, c_line in commits[:15]:
                rec.append(f"  - `{repo_name}`：`{c_line}`")
        else:
            rec.append("- **当日 Git 提交汇总**：（当日无其他本地仓库提交记录）")
        rec_block = "\n".join(rec) + "\n\n"

        if "## 📊 今日完成结算记录" not in updated:
            sep_idx = updated.rfind("\n---\n")
            if sep_idx >= 0:
                updated = updated[:sep_idx + 1] + rec_block + updated[sep_idx + 1:]
            else:
                updated = updated.rstrip() + "\n\n" + rec_block
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as e:
        print("finalize archived md failed:", e, file=sys.stderr)


# ---------- 确认归档（done.ps1 调用：--confirm）----------
def confirm(past_only=False):
    """past_only=True：仅归档今天之前的未确认计划（生成前的补确认弹窗用），
    并作废今天已自动生成的"延续计划"，使随后的重新生成按新题号出题。"""
    today = date.today()
    tiso = today.isoformat()
    state = load_state()
    pend = sorted(d for d, h in state["history"].items()
                  if h.get("status", "pending") == "pending")
    arch = [d for d in pend if (d < tiso or not past_only)]
    if not arch:
        # 兜底：若今天计划尚未生成（如睡眠唤醒漏跑任务），双击「完成今日计划」自动补生成今日计划
        if not past_only and tiso not in state["history"]:
            main(force=True)
            st2 = load_state()
            print("GENERATED_TODAY:" + tiso)
            write_run_result({"result": "generated_today", "date": tiso, "streak": st2.get("streak", 0)})
            return
        print("NOTHING_PENDING")
        write_run_result({"result": "nothing_pending"})
        return
    # 先计算本次确认后的最新 streak，以便写入归档结算记录
    last = state.get("last_confirm")
    if last == tiso:
        new_streak = state.get("streak", 1)
    elif last == (today - timedelta(days=1)).isoformat():
        new_streak = state.get("streak", 0) + 1
    else:
        new_streak = 1

    for iso in arch:
        src = os.path.join(REPO_PLANS, f"{iso}.md")
        dst_dir = os.path.join(REPO_DONE, iso[:7])
        os.makedirs(dst_dir, exist_ok=True)
        dst_path = os.path.join(dst_dir, f"{iso}.md")
        if os.path.exists(src):
            os.replace(src, dst_path)
        h_entry = state["history"][iso]
        h_entry["status"] = "done"
        _finalize_archived_md(iso, dst_path, new_streak, h_entry)
        # Obsidian 镜像：把归档后的计划（含结算记录）移入 done/YYYY-MM/，并清掉进行中那一份
        if OBS_ENABLED:
            try:
                with open(dst_path, "r", encoding="utf-8-sig") as f:
                    archived_md = f.read()
                mirror_plan_to_obsidian(iso, archived_md, archived=True)
                stale = os.path.join(OBS_PLANS, f"{iso}.md")
                if os.path.exists(stale):
                    os.remove(stale)
            except Exception as e:
                print("obsidian archive mirror failed:", e, file=sys.stderr)
        if not h_entry.get("py_stage"):
            mark_lc_note_ac(h_entry["qi"], iso, review=bool(h_entry.get("review")))
        p = os.path.join(desktop_dir(), f"学习计划_{iso}.md")
        if os.path.exists(p):
            os.remove(p)
    # 推进题号：仅按非复习、且当天真的做了 LeetCode 的日子推进
    # （Python 基础关暂替期间不推进，避免把没做的题白白跳过）
    new_qi = [state["history"][d]["qi"] for d in arch
              if not state["history"][d].get("review")
              and not state["history"][d].get("py_stage")]
    if new_qi:
        state["q_index"] = max(state["q_index"], max(new_qi) + 1)
    state["streak"] = new_streak
    state["last_confirm"] = tiso
    # 今天若已被自动生成为"延续计划"，作废该条目 → 随后的重新生成按推进后的题号出新题
    if past_only and tiso in state["history"] and state["history"][tiso].get("status") == "pending":
        del state["history"][tiso]
    save_state(state)
    sync_obsidian_scripts()
    print("CONFIRMED:" + ",".join(arch))
    print(f"STREAK:{state['streak']}")
    write_run_result({"result": "confirmed", "dates": arch,
                      "streak": state["streak"], "past_only": past_only})

# ---------- 自定义计划命令 ----------
def arg_value(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return default

def print_cfg_summary(cfg):
    days = cfg.get("days") or []
    print(f"模式: {cfg.get('mode')}  目标: {cfg.get('goal', '—')}")
    print(f"周期: {cfg.get('start', '—')} ~ {cfg.get('end', '—')}"
          f"（{len(days)} 天，每天约 {cfg.get('daily_minutes', '—')} 分钟）")
    ms = {m["stage"]: m.get("grad", "") for m in cfg.get("milestones") or []}
    cur, first, prev, cnt = None, "", "", 0
    for x in days:
        if x["stage"] != cur:
            if cur is not None:
                g = f"｜毕业：{ms[cur]}" if ms.get(cur) else ""
                print(f"  · {cur}：{first} ~ {prev}（{cnt} 天）{g}")
            cur, first, cnt = x["stage"], x["date"], 0
        prev, cnt = x["date"], cnt + 1
    if cur is not None:
        g = f"｜毕业：{ms[cur]}" if ms.get(cur) else ""
        print(f"  · {cur}：{first} ~ {prev}（{cnt} 天）{g}")

def cmd_new_goal(args):
    goal = arg_value(args, "--new-goal")
    if not goal:
        print('用法: python gen_daily.py --new-goal "目标" [--days 30 | --end 2026-11-15] '
              '[--minutes 120] [--background "背景"]')
        sys.exit(2)
    cfg = load_plan_cfg()
    start = date.today()
    end_s = arg_value(args, "--end")
    end = date.fromisoformat(end_s) if end_s else start + timedelta(days=int(arg_value(args, "--days", "30")) - 1)
    ndays = (end - start).days + 1
    if ndays < 1 or ndays > 90:
        print("计划天数需在 1~90 天之间（更长的目标建议分段制定）")
        sys.exit(2)
    minutes = int(arg_value(args, "--minutes", str(cfg.get("daily_minutes", 120))))
    background = arg_value(args, "--background", cfg.get("background") or DEFAULT_BG)
    print(f"正在调用 LLM 生成 {ndays} 天逐日计划（{llm_cfg(cfg)['model']}）...")
    try:
        plan = gen_custom_plan(goal, start, end, minutes, background, cfg)
    except Exception as e:
        print(f"[失败] {e}")
        sys.exit(1)
    cfg.update({"mode": "custom", "goal": goal, "background": background,
                "daily_minutes": minutes, "start": start.isoformat(), "end": end.isoformat(),
                "milestones": plan["milestones"], "days": plan["days"]})
    save_plan_cfg(cfg)
    print("[OK] 自定义计划已保存：")
    print_cfg_summary(cfg)
    main()

def cmd_import_plan(args):
    path = arg_value(args, "--import-plan")
    if not path or not os.path.exists(path):
        print('用法: python gen_daily.py --import-plan plan.json [--goal "目标"]')
        sys.exit(2)
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    raw = data.get("days") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        print("JSON 里需要非空的 days 数组")
        sys.exit(2)
    days = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict) or not s.get("date"):
            print(f"days[{i}] 缺少 date 字段")
            sys.exit(2)
        days.append({"date": str(s["date"]), "stage": str(s.get("stage") or "自定义"),
                     "main": str(s.get("main") or s.get("task") or ""),
                     "hand": str(s.get("hand") or ""), "ai": str(s.get("ai") or ""),
                     "extra": str(s.get("extra") or "")})
    days.sort(key=lambda x: x["date"])
    if len({x["date"] for x in days}) != len(days):
        print("days 里有重复日期")
        sys.exit(2)
    cfg = load_plan_cfg()
    goal = arg_value(args, "--goal") or (data.get("goal") if isinstance(data, dict) else "") or "自定义计划"
    cfg.update({"mode": "custom", "goal": goal, "start": days[0]["date"], "end": days[-1]["date"],
                "milestones": data.get("milestones", []) if isinstance(data, dict) else [],
                "days": days})
    save_plan_cfg(cfg)
    print(f"[OK] 已导入 {len(days)} 天计划：")
    print_cfg_summary(cfg)
    main()

def cmd_show_plan():
    cfg = load_plan_cfg()
    print_cfg_summary(cfg)
    llm = llm_cfg(cfg)
    print(f"LLM: {llm['base_url']} / {llm['model']} / key={'已配置' if llm.get('api_key') else '未配置'}")


def check_practice():
    """供 done.ps1 在确认归档前调用：自动检测当前待确认计划的练习 .py 文件是否跑通输出 AC!"""
    st = load_state()
    pend = sorted(d for d, h in st["history"].items()
                  if h.get("status", "pending") == "pending")
    if not pend:
        write_run_result({"check": "no_pending"})
        print("CHECK_STATUS:no_pending")
        return
    target_iso = pend[-1]
    h = st["history"][target_iso]
    if h.get("py_stage"):
        write_run_result({"check": "ac", "date": target_iso, "file": h["py_stage"], "q": "Python 基础关"})
        print("CHECK_STATUS:ac")
        return
    qmeta = qmeta_of(h["qi"])
    num, qname, slug = qmeta[0], qmeta[1], qmeta[2]
    prac_path, fname = _find_practice_file(target_iso, num, slug)
    qlabel = f"LC {num}. {qname}"
    if not os.path.exists(prac_path):
        write_run_result({"check": "todo", "date": target_iso, "file": fname,
                          "q": qlabel, "reason": "未找到对应的本地练习文件"})
        print("CHECK_STATUS:todo")
        return
    user_code = _extract_user_code(prac_path)
    if not user_code:
        write_run_result({"check": "todo", "date": target_iso, "file": fname,
                          "q": qlabel, "reason": "练习文件仍是初始 pass 模板，尚未填写手写代码"})
        print("CHECK_STATUS:todo")
        return
    try:
        proc = _run_quiet(
            [sys.executable, "-X", "utf8", prac_path],
            timeout=10
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0 and "AC!" in out:
            write_run_result({"check": "ac", "date": target_iso, "file": fname, "q": qlabel})
            print("CHECK_STATUS:ac")
        else:
            tail = (err or out or f"退出码 {proc.returncode}").splitlines()[-2:]
            reason = " | ".join(tail)
            write_run_result({"check": "fail", "date": target_iso, "file": fname,
                              "q": qlabel, "reason": reason})
            print("CHECK_STATUS:fail")
    except subprocess.TimeoutExpired:
        write_run_result({"check": "fail", "date": target_iso, "file": fname,
                          "q": qlabel, "reason": "运行超时（>10s，可能存在死循环）"})
        print("CHECK_STATUS:fail")
    except Exception as e:
        write_run_result({"check": "fail", "date": target_iso, "file": fname,
                          "q": qlabel, "reason": str(e)})
        print("CHECK_STATUS:fail")


def cmd_stats():
    """打印当前打卡进度、54 关算法闯关进度条与各主题完成度。"""
    st = load_state()
    qi = st.get("q_index", 0)
    total = len(LC_PLAN)
    done_in_round = min(qi, total) if qi < total else qi % total
    round_no = qi // total + 1
    bar_len = 27
    filled = int(round(bar_len * done_in_round / total))
    bar = "█" * filled + "░" * (bar_len - filled)
    station = stage_of(date.today())[0]
    print(f"==== 每日学习计划 · 进度统计 ====")
    print(f"当前技能站: {station}  |  连续打卡: {st.get('streak', 0)} 天  |  总确认天数: {sum(1 for h in st['history'].values() if h.get('status') == 'done')} 天")
    print(f"算法闯关  : [{bar}] {done_in_round}/{total} 关（第 {round_no} 轮）")
    topics = {}
    for i, (tp, qn, _) in enumerate(LC_PLAN):
        topics.setdefault(tp, [0, 0])
        topics[tp][1] += 1
        if i < qi:
            topics[tp][0] += 1
    print("各主题进度:")
    for tp, (d_cnt, t_cnt) in topics.items():
        mark = "✅" if d_cnt == t_cnt else ("🔥" if d_cnt > 0 else "⏳")
        print(f"  {mark} {tp:<8}: {d_cnt}/{t_cnt}")


def _inject_knowledge_field(note_path, num, topic):
    """给 Obsidian 笔记补/更新 YAML frontmatter 里的 knowledge 字段（供知识点页的 Dataview 反查）。
    只在 Obsidian 格式（有 YAML frontmatter）的笔记上操作；learning-notes 纯 Markdown 不处理。"""
    if not note_path or not os.path.exists(note_path):
        return False
    main_k, rel_k = _knowledge_links(num, topic)
    if not main_k:
        return False
    try:
        with open(note_path, "r", encoding="utf-8-sig") as f:
            nt = f.read()
        if not nt.startswith("---\n"):
            return False
        end = nt.find("\n---", 4)
        if end < 0:
            return False
        fm = nt[4:end]
        body = nt[end + 4:]
        # 已有 knowledge 字段：整段替换；否则插到 frontmatter 末尾
        block = ["knowledge:"] + [f'  - "{main_k}"']
        if rel_k:
            block.append("knowledge_related:")
            block += [f'  - "{r}"' for r in rel_k]
        lines = fm.splitlines()
        removed, kept, skipping = [], [], False
        for line in lines:
            if line.startswith("knowledge:") or line.startswith("knowledge_related:"):
                skipping = True
                removed.append(line)
                continue
            if skipping:
                if line.startswith("  - "):
                    removed.append(line)
                    continue
                skipping = False
            kept.append(line)
        new_fm = "\n".join(kept + block)
        _write_lf(note_path, "---\n" + new_fm + nt[end:])
        return True
    except Exception as e:
        print("inject knowledge failed:", e, file=sys.stderr)
        return False


def sync_notes_history():
    """将 state.json 中历史已刷过的所有题目回填到 learning-notes 与 Obsidian 两个笔记库。"""
    if not _note_stores():
        print("未找到 leetcode practice 目录")
        return
    st = load_state()
    first_date = {}
    first_prac = {}
    review_date = {}
    done_qis = set()
    seen_qis = []

    for iso, h in sorted(st["history"].items()):
        qi = h["qi"]
        qmeta = qmeta_of(qi)
        num, slug = qmeta[0], qmeta[2]
        p_path, _ = _find_practice_file(iso, num, slug)
        if qi not in first_date:
            first_date[qi] = iso
            first_prac[qi] = p_path
            seen_qis.append(qi)
        if h.get("review") and h.get("status") == "done":
            review_date[qi] = iso
        if h.get("status") == "done":
            done_qis.add(qi)

    flagged = _flagged_review_qnums()
    rows = []
    for qi in sorted(set(seen_qis)):
        qmeta = qmeta_of(qi)
        topic = lc_entry(qi)[0]
        num, qname = qmeta[0], qmeta[1]
        diff_zh = DIFF_ZH.get(qmeta[3], qmeta[3])
        iso = first_date[qi]
        prac_path = first_prac[qi]
        ensure_lc_note(qmeta, topic, iso, prac_path)

        status_str = "需二刷" if num in flagged else ("AC" if qi in done_qis else "进行中")
        note_fname = f"{num:04d}-{qname}.md"
        for store in _note_stores():
            note_path = os.path.join(store["root"], "notes", note_fname)
            _set_note_state(note_path, num, qname, status_str,
                            iso=review_date.get(qi), review=(qi in review_date),
                            obsidian=(store["style"] == "obsidian"))
            if store["style"] == "obsidian":
                _inject_knowledge_field(note_path, num, topic)
        rows.append(f"| {num:04d} | [{qname}](notes/{note_fname}) | {diff_zh} | {topic} | {status_str} | {iso} |")

    readme_path = os.path.join(LC_NOTES_DIR, "README.md")
    if os.path.exists(readme_path) and rows:
        with open(readme_path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        new_lines = []
        in_table = False
        table_replaced = False
        for line in lines:
            if line.strip().startswith("|---"):
                in_table = True
                new_lines.append(line)
                new_lines.extend(rows)
                table_replaced = True
                continue
            if in_table:
                if line.strip().startswith("|"):
                    continue
                else:
                    in_table = False
            new_lines.append(line)
        if table_replaced:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
    print(f"[OK] 已同步 {len(rows)} 道历史题目至 {LC_NOTES_DIR}")
    if OBS_ENABLED:
        print(f"[OK] 同一批题目已同步至 Obsidian：{OBS_LC_NOTES}")


def cmd_mark_review(args):
    """将指定题号标记为『需二刷』（下个周日智能复习优先抽取）。用法: python gen_daily.py --mark-review 739"""
    val = arg_value(args, "--mark-review")
    if not val or not val.isdigit():
        print("用法: python gen_daily.py --mark-review <题号，如 739>")
        sys.exit(2)
    target_num = int(val)
    if target_num not in QN2I:
        print(f"题库中未找到题号 {target_num}")
        sys.exit(2)
    qmeta = Q[QN2I[target_num]]
    qname = qmeta[1]
    note_fname = f"{target_num:04d}-{qname}.md"
    for store in _note_stores():
        note_path = os.path.join(store["root"], "notes", note_fname)
        _set_note_state(note_path, target_num, qname, "需二刷",
                        obsidian=(store["style"] == "obsidian"))
    readme_path = os.path.join(LC_NOTES_DIR, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8-sig") as f:
            rc = f.read()
        pattern = rf"^(\|\s*{target_num:04d}\s*\|[^|]+\|[^|]+\|[^|]+\|\s*)(?:进行中|未开始|AC)(\s*\|[^|]+\|)$"
        rc_new = re.sub(pattern, r"\1需二刷\2", rc, flags=re.MULTILINE)
        if rc_new != rc:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(rc_new)
    print(f"[OK] 已将 LC {target_num}. {qname} 标记为『需二刷』，下个周日将优先抽考！")


def cmd_sync_obsidian(args):
    """把 dailyPlan 仓库、刷题笔记库、IELTS 周记与脚本一次性全量镜像进 Obsidian 库。"""
    if not OBS_ENABLED:
        print(f"未找到 Obsidian 库：{OBSIDIAN_VAULT}（需要 .obsidian 目录）")
        return
    copied_scripts = sync_obsidian_scripts()
    n_plan = n_done = n_prac = n_ielts = 0

    # 进行中的计划
    if os.path.isdir(REPO_PLANS):
        for fn in sorted(os.listdir(REPO_PLANS)):
            if not fn.endswith(".md"):
                continue
            iso = fn[:-3]
            with open(os.path.join(REPO_PLANS, fn), "r", encoding="utf-8-sig") as f:
                md = f.read()
            if mirror_plan_to_obsidian(iso, md):
                n_plan += 1

    # 已归档的计划
    if os.path.isdir(REPO_DONE):
        for month in sorted(os.listdir(REPO_DONE)):
            mdir = os.path.join(REPO_DONE, month)
            if not os.path.isdir(mdir):
                continue
            for fn in sorted(os.listdir(mdir)):
                if not fn.endswith(".md"):
                    continue
                iso = fn[:-3]
                with open(os.path.join(mdir, fn), "r", encoding="utf-8-sig") as f:
                    md = f.read()
                if mirror_plan_to_obsidian(iso, md, archived=True):
                    n_done += 1

    # 手写练习
    if os.path.isdir(PRAC_DIR):
        for fn in sorted(os.listdir(PRAC_DIR)):
            if fn.endswith(".py") and mirror_practice_to_obsidian(fn):
                n_prac += 1

    # 刷题笔记（两个库都会补齐 / 校正状态）
    sync_notes_history()

    # IELTS：只补 Obsidian 里缺失的周，不覆盖已有手写内容
    src_ielts = os.path.join(LEARNING_NOTES_DIR, "IELTS")
    if os.path.isdir(src_ielts) and os.path.isdir(OBS_IELTS):
        for fn in sorted(os.listdir(src_ielts)):
            m = re.search(r"(\d+)", fn)
            if not fn.lower().endswith((".md", ".txt")) or not m:
                continue
            week = int(m.group(1))
            target = os.path.join(OBS_IELTS, f"IELTS Week {week}.md")
            if os.path.exists(target):
                continue
            try:
                with open(os.path.join(src_ielts, fn), "r", encoding="utf-8-sig") as rf:
                    _write_lf(target, rf.read())
                n_ielts += 1
            except Exception as e:
                print("ielts mirror failed:", fn, e, file=sys.stderr)

    print(f"[OK] Obsidian 镜像完成：脚本 {len(copied_scripts)} 个｜进行中计划 {n_plan}｜归档计划 {n_done}"
          f"｜练习 {n_prac}｜新增 IELTS {n_ielts}")
    print(f"     库路径：{OBSIDIAN_VAULT}")
    write_run_result({"result": "obsidian_synced", "scripts": len(copied_scripts),
                      "plans": n_plan, "done": n_done, "practice": n_prac, "ielts": n_ielts})


def cmd_open():
    """一键开启今日学习工作台：若今日未生成先生成 → VS Code 同时打开计划/练习.py/刷题笔记 → 浏览器打开题目页。"""
    import webbrowser
    tiso = date.today().isoformat()
    st = load_state()
    if tiso not in st["history"]:
        main(force=True)
        st = load_state()
    h = st["history"].get(tiso) or {}
    qi = h.get("qi", st.get("q_index", 0))
    qmeta = qmeta_of(qi)
    topic = lc_entry(qi)[0]
    num, qname, slug = qmeta[0], qmeta[1], qmeta[2]
    prac_full, _ = _find_practice_file(tiso, num, slug)
    note_path = ensure_lc_note(qmeta, topic, tiso, prac_full)
    dp = os.path.join(desktop_dir(), f"学习计划_{tiso}.md")
    plan_file = dp if os.path.exists(dp) else os.path.join(REPO_PLANS, f"{tiso}.md")
    if not os.path.exists(plan_file):
        done_file = os.path.join(REPO_DONE, tiso[:7], f"{tiso}.md")
        if os.path.exists(done_file):
            plan_file = done_file

    # 用 Obsidian 库里的版本优先（你在 Obsidian 里看笔记），否则回退到仓库版本
    prac_obs = os.path.join(OBS_PRAC, os.path.basename(prac_full))
    note_obs = os.path.join(OBS_LC_NOTES, os.path.basename(note_path))
    plan_obs = os.path.join(OBS_PLANS, f"{tiso}.md")
    if not os.path.exists(plan_obs):
        plan_obs = os.path.join(OBS_DONE, tiso[:7], f"{tiso}.md")
    plan_file = plan_obs if (OBS_ENABLED and os.path.exists(plan_obs)) else plan_file
    if OBS_ENABLED and os.path.exists(note_obs):
        note_path = note_obs
    if OBS_ENABLED and os.path.exists(prac_obs):
        prac_full = prac_obs

    files_to_open = [p for p in (plan_file, prac_full, note_path) if p and os.path.exists(p)]
    # 只用 Code.exe（GUI 程序，不会创建控制台）；"code" 是 .cmd 包装，会闪黑窗
    code_candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ]
    code_exe = next((c for c in code_candidates if os.path.exists(c)), None)
    if code_exe:
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = _NO_WINDOW
            subprocess.Popen([code_exe] + files_to_open, **kwargs)
        except Exception as e:
            print("open vscode failed:", e, file=sys.stderr)
    else:
        print("VS Code Code.exe not found; skip opening editor", file=sys.stderr)

    url = f"https://leetcode.cn/problems/{slug}/"
    try:
        webbrowser.open(url)
    except Exception as e:
        print("open browser failed:", e, file=sys.stderr)

    write_run_result({"result": "opened", "date": tiso, "q": f"LC {num}. {qname}", "files": files_to_open, "url": url})
    print(f"[OPENED] {tiso} LC {num}. {qname}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        # 供 run_daily.ps1 弹窗决策：是否有未确认遗留 / 今天是否已生成过
        st = load_state()
        t = date.today().isoformat()
        pend = sorted(d for d, h in st["history"].items()
                      if h.get("status", "pending") == "pending" and d < t)
        print("CARRY:" + (",".join(pend) if pend else "none"))
        print("TODAY_ENTRY:" + ("yes" if t in st["history"] else "no"))
    elif "--check-practice" in args:
        check_practice()
    elif "--confirm" in args:
        confirm(past_only="--past-only" in args)
    elif "--sync-notes" in args:
        sync_notes_history()
    elif "--sync-obsidian" in args:
        cmd_sync_obsidian(args)
    elif "--mark-review" in args:
        cmd_mark_review(args)
    elif "--open" in args:
        cmd_open()
    elif "--stats" in args:
        cmd_stats()
    elif "--new-goal" in args:
        cmd_new_goal(args)
    elif "--import-plan" in args:
        cmd_import_plan(args)
    elif "--builtin" in args:
        cfg = load_plan_cfg()
        cfg["mode"] = "builtin"
        save_plan_cfg(cfg)
        print("[OK] 已切回内置计划")
        main(force=True)
    elif "--show-plan" in args:
        cmd_show_plan()
    else:
        main()
