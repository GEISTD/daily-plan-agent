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
import json, os, random, sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE, "logs")
STATE_FILE = os.path.join(BASE, "state.json")
# dailyPlan 归档仓库：默认取与本目录平级的 dailyPlan/（plans/=进行中 done/=已确认 practice/=手写练习）
# 布局不同或想放别处？用环境变量 DAILY_PLAN_REPO 指向仓库根目录即可
DAILY_REPO = os.environ.get("DAILY_PLAN_REPO") or os.path.join(os.path.dirname(BASE), "dailyPlan")
REPO_PLANS = os.path.join(DAILY_REPO, "plans")
REPO_DONE = os.path.join(DAILY_REPO, "done")
PRAC_DIR = os.path.join(DAILY_REPO, "practice")
for d in (LOGS_DIR, REPO_PLANS, REPO_DONE, PRAC_DIR):
    os.makedirs(d, exist_ok=True)

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

# ---------- LeetCode 题库：(题号, 题名, slug, 难度, 标签, 方法签名, 思路提示, 用例) ----------
# 标签: ll=链表 tree=二叉树 design=设计题；用例 None = 给测试建议注释
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
 (15,"三数之和","3sum","Medium",set(),"def threeSum(self, nums: List[int]) -> List[List[int]]:",
  "排序后固定一个数，剩下用对撞双指针；三处去重（固定位、左右指针）",None),
 (1,"两数之和","two-sum","Easy",set(),"def twoSum(self, nums: List[int], target: int) -> List[int]:",
  "哈希表扫一遍：查 target-x 是否已在表中，在则返回，不在则存入",[([[2,7,11,15],9],[0,1])]),
 (206,"反转链表","reverse-linked-list","Easy",{"ll"},"def reverseList(self, head: Optional[ListNode]) -> Optional[ListNode]:",
  "三指针迭代 pre/cur/nxt：逐个断开指向 pre；或递归",[([[1,2,3,4,5]],[5,4,3,2,1])]),
 (21,"合并两个有序链表","merge-two-sorted-lists","Easy",{"ll"},"def mergeTwoLists(self, list1: Optional[ListNode], list2: Optional[ListNode]) -> Optional[ListNode]:",
  "哑节点 dummy + 双指针逐个接较小节点，剩余直接接上",[([[1,2,4],[1,3,4]],[1,1,2,3,4,4])]),
 (141,"环形链表","linked-list-cycle","Easy",{"ll"},"def hasCycle(self, head: Optional[ListNode]) -> bool:",
  "快慢指针（1步/2步），相遇即有环；None 则无环",None),
 (876,"链表的中间结点","middle-of-the-linked-list","Easy",{"ll"},"def middleNode(self, head: Optional[ListNode]) -> Optional[ListNode]:",
  "快慢指针同起点，快到尾时慢在中间（偶数个取第二个中间）",[([[1,2,3,4,5]],[3,4,5])]),
 (20,"有效的括号","valid-parentheses","Easy",set(),"def isValid(self, s: str) -> bool:",
  "栈：左括号入栈，右括号检查栈顶是否匹配；最后栈空才合法",[(["()[]{}"],True),(["(]"],False)]),
 (155,"最小栈","min-stack","Medium",{"design"},"class MinStack:  # 每个方法都要实现",
  "辅助栈：与主栈同步 push，栈顶永远是当前最小值（可存 (val,min) 元组）",None),
 (232,"用栈实现队列","implement-queue-using-stacks","Easy",{"design"},"class MyQueue:  # push/peek/pop/empty",
  "双栈：in 栈管入队，peek/pop 时若 out 栈空则把 in 全部倒进 out",None),
 (739,"每日温度","daily-temperatures","Medium",set(),"def dailyTemperatures(self, temperatures: List[int]) -> List[int]:",
  "单调递减栈存下标；当前温度更高时弹栈，距离=下标差",[([[73,74,75,71,69,72,76,73]],[1,1,4,2,1,1,0,0])]),
 (225,"用队列实现栈","implement-stack-using-queues","Easy",{"design"},"class MyStack:  # push/top/pop/empty",
  "单队列：每次 push 后把前面的元素依次出队再入队，新元素就到了队首",None),
 (42,"接雨水","trapping-rain-water","Hard",set(),"def trap(self, height: List[int]) -> int:",
  "对撞双指针+左右最大高度：每次处理较矮一侧（选做，先理解按列求 min(左max,右max)-h）",[([[0,1,0,2,1,0,1,3,2,1,2,1]],6)]),
 (49,"字母异位词分组","group-anagrams","Medium",set(),"def groupAnagrams(self, strs: List[str]) -> List[List[str]]:",
  "排序后的字符串作为哈希 key 分组（或用 26 字母计数元组）",None),
 (349,"两个数组的交集","intersection-of-two-arrays","Easy",set(),"def intersection(self, nums1: List[int], nums2: List[int]) -> List[int]:",
  "两个 set 求交集即可",None),
 (128,"最长连续序列","longest-consecutive-sequence","Medium",set(),"def longestConsecutive(self, nums: List[int]) -> int:",
  "哈希集合；只从 x-1 不存在的起点开始数 x+1,x+2…，保证 O(n)",[([[100,4,200,1,3,2]],4)]),
 (202,"快乐数","happy-number","Easy",set(),"def isHappy(self, n: int) -> bool:",
  "哈希集合检测循环：重复出现且不为 1 就不快乐（也可用快慢指针）",[([19],True),([2],False)]),
 (350,"两个数组的交集 II","intersection-of-two-arrays-ii","Easy",set(),"def intersect(self, nums1: List[int], nums2: List[int]) -> List[int]:",
  "哈希表计数：短表计数，长表每匹配一个就计数-1 并加入答案",None),
 (704,"二分查找","binary-search","Easy",set(),"def search(self, nums: List[int], target: int) -> int:",
  "标准闭区间二分 [l,r]：l<=r，mid=(l+r)//2，找不到返回 -1",[([[-1,0,3,5,9,12],9],4)]),
 (35,"搜索插入位置","search-insert-position","Easy",set(),"def searchInsert(self, nums: List[int], target: int) -> int:",
  "二分找第一个 ≥ target 的位置；全部小于 target 则返回 len",[([[1,3,5,6],5],2)]),
 (34,"查找元素首尾位置","find-first-and-last-position-of-element-in-sorted-array","Medium",set(),"def searchRange(self, nums: List[int], target: int) -> List[int]:",
  "两次二分：一次找左边界（≥target 第一个），一次找右边界（≤target 最后一个）",None),
 (33,"搜索旋转排序数组","search-in-rotated-sorted-array","Medium",set(),"def search(self, nums: List[int], target: int) -> int:",
  "二分时判断哪半边有序（nums[mid] 与 nums[l] 比较），再决定 target 是否在有序半边",None),
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
  "后序递归：左右子树各找到一个 → 当前节点就是 LCA；自己命中则返回自己",None),
 (112,"路径总和","path-sum","Easy",{"tree"},"def hasPathSum(self, root: Optional[TreeNode], targetSum: int) -> bool:",
  "DFS 每经过节点减去其值，到叶子判剩余是否为 0",[([[5,4,8,11,None,13,4,7,2,None,None,None,1],22],True)]),
 (124,"二叉树最大路径和","binary-tree-maximum-path-sum","Hard",{"tree"},"def maxPathSum(self, root: Optional[TreeNode]) -> int:",
  "后序：贡献值=node.val+max(0,左贡献,右贡献)；经过当前节点的路径=node+左贡献+右贡献",None),
 (200,"岛屿数量","number-of-islands","Medium",set(),"def numIslands(self, grid: List[List[str]]) -> int:",
  "遍历网格，遇到 '1' 计数+1 并 DFS/BFS 把相邻陆地全部淹没成 '0'",[([[["1","1","0","0","0"],["1","1","0","0","0"],["0","0","1","0","0"],["0","0","0","1","1"]]],3)]),
 (695,"岛屿的最大面积","max-area-of-islands","Medium",set(),"def maxAreaOfIsland(self, grid: List[List[int]]) -> int:",
  "DFS 返回当前岛屿面积（1+四方向之和），全程维护最大值",None),
 (994,"腐烂的橘子","rotting-oranges","Medium",set(),"def orangesRotting(self, grid: List[List[int]]) -> int:",
  "多源 BFS：所有腐烂橘子同时入队，按分钟层序扩散；最后还有新鲜橘子返回 -1",None),
 (207,"课程表","course-schedule","Medium",set(),"def canFinish(self, numCourses: int, prerequisites: List[List[int]]) -> bool:",
  "拓扑排序：建邻接表+入度数组，入度 0 入队，出队即学完并解锁后续；全部学完=无环",None),
 (70,"爬楼梯","climbing-stairs","Easy",set(),"def climbStairs(self, n: int) -> int:",
  "dp[i]=dp[i-1]+dp[i-2]；只需两个变量滚动，O(1) 空间",[([2],2),([3],3),([4],5)]),
 (198,"打家劫舍","house-robber","Medium",set(),"def rob(self, nums: List[int]) -> int:",
  "dp：当前房 max(不偷=上一家最优, 偷=上上家+当前)；两个变量滚动",[([[1,2,3,1]],4)]),
 (53,"最大子数组和","maximum-subarray","Medium",set(),"def maxSubArray(self, nums: List[int]) -> int:",
  "以 i 结尾的最大和=max(nums[i], 之前最优+nums[i])，滚动维护全局最大",[([[-2,1,-3,4,-1,2,1,-5,4]],6)]),
 (322,"零钱兑换","coin-change","Medium",set(),"def coinChange(self, coins: List[int], amount: int) -> int:",
  "完全背包：dp[金额]=最少硬币数，初始化无穷大，逐硬币更新",None),
 (62,"不同路径","unique-paths","Medium",set(),"def uniquePaths(self, m: int, n: int) -> int:",
  "二维 dp：dp[i][j]=dp[i-1][j]+dp[i][j-1]，首行首列全 1",[([3,7],28)]),
 (1143,"最长公共子序列","longest-common-subsequence","Medium",set(),"def longestCommonSubsequence(self, text1: str, text2: str) -> int:",
  "二维 dp：字符相等取对角+1，否则取上/左的较大值",None),
 (46,"全排列","permutations","Medium",set(),"def permute(self, nums: List[int]) -> List[List[int]]:",
  "回溯 + used 数组：每层选一个未使用元素，撤销选择",None),
 (78,"子集","subsets","Medium",set(),"def subsets(self, nums: List[int]) -> List[List[int]]:",
  "回溯：每个元素选或不选，每次进入递归都收集当前路径",None),
 (39,"组合总和","combination-sum","Medium",set(),"def combinationSum(self, candidates: List[int], target: int) -> List[List[int]]:",
  "回溯+可重复选（start 不递增）；先排序便于剩余值为负时剪枝",None),
]

# SQL 速通（仅 10-12~10-14）
SQL_DAYS = {
 "2026-10-12": "SQL Day1：单表查询（WHERE/ORDER/LIKE/IN/IS NULL）→ 刷 LeetCode 175、176、177",
 "2026-10-13": "SQL Day2：GROUP BY/HAVING/JOIN/子查询 → 刷 181、182、183",
 "2026-10-14": "SQL Day3：窗口函数 ROW_NUMBER/RANK + RAG 元数据过滤场景 → 刷 184、185、197",
}

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
    for a,b,*rest in STAGES:
        if a <= iso <= b:
            return rest
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

def build_practice(qi, qmeta, d, review=False):
    num, name, slug, diff, tags, sig, hint, cases = qmeta
    suffix = f"_r{qi+1}" if review else ""
    fname = f"{d.strftime('%m-%d')}_lc{num}_{slug}{('_v'+str(suffix)) if review else ''}.py"
    path = os.path.join(PRAC_DIR, fname)
    if os.path.exists(path):
        return path, fname  # 幂等：不覆盖已生成（尤其已手写）的文件

    round_tag = f"第 {suffix} 轮复习" if review else "新题"
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
    if "design" in tags or cases is None:
        L.append(DESIGN_TEST_COMMENT.rstrip("\n"))
    else:
        L.append("def test():")
        L.append("    sol = Solution()")
        test_idx = 0
        for args, expected in cases:
            test_idx += 1
            if "ll" in tags:
                for i, a in enumerate(args):
                    L.append(f"    a{i} = build_list({a!r})")
                if num == 876:
                    L.append(f"    got = to_list(sol.{sig.split('(')[0].split()[-1]}({', '.join(f'a{i}' for i in range(len(args)))}))")
                    L.append(f"    assert got == {expected!r}, (got, {expected!r})")
                elif num == 141:
                    pass
                else:
                    L.append(f"    got = to_list(sol.{sig.split('(')[0].split()[-1]}({', '.join(f'a{i}' for i in range(len(args)))}))")
                    L.append(f"    assert got == {expected!r}, (got, {expected!r})")
            elif "tree" in tags:
                L.append(f"    t0 = build_tree({args[0]!r})")
                call_args = ["t0"] + [repr(a) for a in args[1:]]
                method = sig.split('(')[0].split()[-1]
                if num in (144, 94):
                    L.append(f"    assert sol.{method}(t0) == {expected!r}")
                elif num == 102:
                    L.append(f"    assert sol.{method}(t0) == {expected!r}")
                elif num == 226:
                    L.append(f"    assert level_order(sol.{method}(t0)) == {expected!r}")
                else:
                    L.append(f"    assert sol.{method}({', '.join(call_args)}) == {expected!r}")
            else:
                # 普通题：参数可能是 list/nested list，元数据里 list 入参包了一层 [..]
                method = sig.split('(')[0].split()[-1]
                if "inplace" in tags:
                    L.append(f"    nums = {args[0]!r}")
                    L.append(f"    sol.{method}(nums)")
                    L.append(f"    assert nums == {expected!r}, 'case {test_idx}'")
                else:
                    rendered = []
                    for a in args:
                        rendered.append(repr(a))
                    L.append(f"    assert sol.{method}({', '.join(rendered)}) == {expected!r}, 'case {test_idx}'")
        L.append('    print("AC!")')
        L.append("")
        L.append('if __name__ == "__main__":')
        L.append("    test()")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path, fname

def pick_question(d, state):
    """返回 (qmeta, review_bool, qi)。周日做本周复习；一轮刷完进入二刷。"""
    is_sunday = d.weekday() == 6
    if is_sunday and state["history"]:
        week_ago = (d - timedelta(days=6)).isoformat()
        done = [(iso, h["qi"]) for iso, h in state["history"].items()
                if iso >= week_ago and not h.get("review")]
        if done:
            iso, qi = random.choice(done)
            return Q[qi % len(Q)], True, qi
    qi = state["q_index"]
    if qi >= len(Q):
        qi = qi % len(Q)  # 二刷
        review = True
    else:
        review = False
    return Q[qi], review, qi

def build_md(d, plan, qmeta, qi, review, qfname, state, carry=None):
    iso = d.isoformat()
    wd = WEEKDAYS[d.weekday()]
    num, qname, slug, diff, tags, sig, hint, cases = qmeta
    round_no = state["q_index"] // len(Q) + 1
    qtag = "复习重做" if review else f"第 {qi+1}/{len(Q)} 题（第 {round_no} 轮）"
    is_sunday = d.weekday() == 6
    sql_line = plan.get("extra") or None
    station, main_task = plan["station"], plan["main"]
    hand, ai_help, grad = plan["hand"], plan["ai"], plan["grad"]
    L = []
    L.append(f"# 今日学习计划 · {iso} {wd}")
    L.append("")
    L.append(f"**当前技能站：{station}** ｜ 连续打卡：**{state['streak']} 天** ｜ 算法：{qtag}")
    if plan.get("goal"):
        L.append(f" ｜ 🎯 **{plan['goal']}**（Day {plan['day_no']}/{plan['day_total']}）")
    L.append("")
    if carry:
        L.append("> ⚠️ 有未确认的遗留计划：" + "、".join(carry) +
                 "。完成后双击桌面「✅完成今日计划」一并归档；未确认前不推进新题。")
        L.append("")
    L.append("## 1. 主任务（45-90 分钟，当天必须有 commit）")
    L.append(f"- {main_task}")
    L.append(f"- ✍️ **必须手写**：{hand}")
    L.append(f"- 🤖 **可交给 AI**：{ai_help}")
    L.append(f"- 🎯 本站毕业标准：{grad}")
    L.append("")
    L.append("## 2. 碎片任务")
    L.append(f"- [ ] **算法手写 20min**：LeetCode {num}. {qname} [{diff}]（{qtag}）")
    L.append(f"      练习文件已生成：`{os.path.join(PRAC_DIR, qfname)}` —— 在 VS Code 打开手写，本地跑通看到 AC!（禁止纸上默写）")
    L.append(f"      题目链接：https://leetcode.cn/problems/{slug}/")
    if sql_line:
        L.append(f"- [ ] **{sql_line}**")
    L.append("- [ ] **英语 30min**：GRE 单词 + 英文技术阅读")
    L.append("")
    if is_sunday:
        L.append("## 3. 周日特殊任务")
        L.append("- [ ] **重写日 30min**：把本周 AI 帮写最多的一段核心代码删掉，凭理解重写一遍")
        L.append("- [ ] **Demo Day 30min**：做一个可展示产出（截图/录屏/GIF），写进 learning-notes/progress.md 产出墙")
        L.append("- [ ] **概念卡 10min**：复习本周+上周的概念卡")
        L.append("")
        L.append("## 4. 手写 vs AI 红线")
    else:
        L.append("## 3. 手写 vs AI 红线")
    L.append("- 顺序永远是 **先自己写 → 跑通 → 再让 AI review**，不要反过来")
    L.append("- 不粘贴没读懂的长代码；核心代码（损失/训练循环/检索/评估/路由）必须自己写")
    L.append("")
    L.append(f"## {5 if is_sunday else 4}. 完成后（确认归档）")
    L.append("1. 练习文件本地跑通看到 `AC!`，主任务当天有 commit")
    L.append("2. **双击桌面「✅完成今日计划」**：本计划自动归档到 GitHub dailyPlan 仓库并删除本地文件，之后才推进新题")
    L.append("3. 没完成就不点确认：文件保留，明天继续攻克同一题（最小方案：练习文件写 1 行也是进步）")
    L.append("")
    L.append(f"---\n_由 gen_daily.py 于 {datetime.now().strftime('%Y-%m-%d %H:%M')} 自动生成_")
    return "\n".join(L)

def main():
    today = date.today()
    state = load_state()
    iso = today.isoformat()

    # 幂等：同一天重复运行不推进（连续打卡与题号由 --confirm 确认时结算）
    state["last_date"] = iso

    def pending_dates(today_iso):
        return sorted(d for d, h in state["history"].items()
                      if h.get("status", "pending") == "pending" and d < today_iso)

    carry = []
    if iso in state["history"]:
        h = state["history"][iso]
        qi, review = h["qi"], h["review"]
        qmeta = Q[qi % len(Q)]
        carry = pending_dates(iso)
    else:
        pend = pending_dates(iso)
        if pend:
            # 有未确认的遗留计划：不推进新题，继续攻克最早未完成的那题
            h0 = state["history"][pend[0]]
            qi, review = h0["qi"], h0["review"]
            qmeta = Q[qi % len(Q)]
            carry = pend
        else:
            qmeta, review, qi = pick_question(today, state)
        state["history"][iso] = {"qi": qi, "review": review,
                                 "station": stage_of(today)[0], "status": "pending"}

    path, qfname = build_practice(qi, qmeta, today, review=review)
    plan = plan_of(today)
    state["history"][iso]["station"] = plan["station"]
    md = build_md(today, plan, qmeta, qi, review, qfname, state, carry=carry)

    plan_path = os.path.join(REPO_PLANS, f"{iso}.md")
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(md)

    desktop_ok = False
    try:
        dp = os.path.join(desktop_dir(), f"学习计划_{iso}.md")
        with open(dp, "w", encoding="utf-8") as f:
            f.write(md)
        desktop_ok = True
    except Exception as e:
        print("desktop write failed:", e, file=sys.stderr)

    save_state(state)

    # toast 摘要（ps1 读取，避免中文硬编码在 ps1）
    summary = os.path.join(BASE, "last_summary.txt")
    qnum, qname = qmeta[0], qmeta[1]
    with open(summary, "w", encoding="utf-8") as f:
        f.write(f"今日计划已生成 | 连续 {state['streak']} 天\n")
        f.write(f"{plan['station']}：{qnum}.{qname}（{'复习' if review else '新题'}）已就位\n")

    print(f"[OK] {iso} {WEEKDAYS[today.weekday()]} streak={state['streak']}")
    print(f"plan: {plan_path}")
    print(f"practice: {path}")
    print(f"desktop: {desktop_ok}")

# ---------- 确认归档（done.ps1 调用：--confirm）----------
def confirm():
    today = date.today()
    tiso = today.isoformat()
    state = load_state()
    pend = sorted(d for d, h in state["history"].items()
                  if h.get("status", "pending") == "pending")
    if not pend:
        print("NOTHING_PENDING")
        return
    for iso in pend:
        src = os.path.join(REPO_PLANS, f"{iso}.md")
        dst_dir = os.path.join(REPO_DONE, iso[:7])
        os.makedirs(dst_dir, exist_ok=True)
        if os.path.exists(src):
            os.replace(src, os.path.join(dst_dir, f"{iso}.md"))
        state["history"][iso]["status"] = "done"
        p = os.path.join(desktop_dir(), f"学习计划_{iso}.md")
        if os.path.exists(p):
            os.remove(p)
    # 推进题号：仅按非复习题推进（多个遗留日共用同一题只 +1）
    new_qi = [state["history"][d]["qi"] for d in pend
              if not state["history"][d].get("review")]
    if new_qi:
        state["q_index"] = max(state["q_index"], max(new_qi) + 1)
    # 连续打卡按确认日结算
    last = state.get("last_confirm")
    if last == tiso:
        pass
    elif last == (today - timedelta(days=1)).isoformat():
        state["streak"] = state.get("streak", 0) + 1
    else:
        state["streak"] = 1
    state["last_confirm"] = tiso
    save_state(state)
    print("CONFIRMED:" + ",".join(pend))
    print(f"STREAK:{state['streak']}")

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

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--confirm" in args:
        confirm()
    elif "--new-goal" in args:
        cmd_new_goal(args)
    elif "--import-plan" in args:
        cmd_import_plan(args)
    elif "--builtin" in args:
        cfg = load_plan_cfg()
        cfg["mode"] = "builtin"
        save_plan_cfg(cfg)
        print("[OK] 已切回内置计划")
        main()
    elif "--show-plan" in args:
        cmd_show_plan()
    else:
        main()
