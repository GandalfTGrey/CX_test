你是一名资深深度学习研究员、Diffusion 模型工程师、自动驾驶算法研究员和技术文档工程师。你现在需要在当前 GitHub 代码库中完成一个系统性学习文档项目，主题是：

# Diffusion 原理、关键论文技术栈、代码复现与自动驾驶应用学习文档

目标不是简单写一篇综述，而是在代码库中构建一个可持续维护的学习文档体系，使读者能够按照“知识树 → 数学公式 → 推理过程 → 代码实现 → 论文对应 → 自动驾驶应用”的逻辑逐步学习 diffusion。

---

## 一、工作目标

请围绕以下三个方向完成文档建设：

## 2.1 Diffusion 原理学习文档

按照知识点和知识树结构组织 diffusion 的核心内容，包括：

1. Diffusion 的直觉解释；
2. Forward process / 加噪过程；
3. Reverse process / 去噪过程；
4. Noise schedule；
5. DDPM 的训练目标；
6. Score matching 视角；
7. Denoising score function；
8. DDIM / fast sampling；
9. Latent diffusion；
10. Classifier guidance / classifier-free guidance；
11. Diffusion Policy / action diffusion；
12. Trajectory diffusion；
13. 自动驾驶 diffusion planner 中 noisy trajectory、condition、guidance、reward、closed-loop evaluation 的含义。

每个知识点都必须按照以下模板组织：

```text
知识点名称：
一句话解释：
解决什么问题：
直观理解：
数学公式：
公式中每个符号的含义：
推理过程：
对应代码块：
代码每一行对应的公式含义：
常见误解：
和其他知识点的关系：
代表论文：
```

要求公式、推理、代码块一一对应。例如：

```text
公式：
x_t = sqrt(alpha_bar_t) * x_0 + sqrt(1 - alpha_bar_t) * epsilon

代码：
x_t = sqrt_alpha_bar_t * x_0 + sqrt_one_minus_alpha_bar_t * noise

解释：
sqrt_alpha_bar_t 对应 sqrt(alpha_bar_t)，表示保留原始 clean data 的比例；
sqrt_one_minus_alpha_bar_t 对应 sqrt(1 - alpha_bar_t)，表示注入噪声的比例；
noise 对应 epsilon，来自标准高斯分布。
```

---

## 2.2 Diffusion 相关 paper 整理与复现映射

请整理 diffusion 技术栈中的关键论文，并把每篇论文拆解为：

1. 论文基本信息；
2. 研究问题；
3. 核心贡献；
4. 对 diffusion 技术栈的贡献位置；
5. 关键数学公式；
6. 关键算法流程；
7. 代码复现要点；
8. 如果当前代码库已有相关实现，请定位到具体文件、函数、类；
9. 如果当前代码库没有实现，请给出 minimal pseudo-code 或建议新增的 toy example；
10. 论文和知识树中哪些知识点对应；
11. 论文的局限性；
12. 适合读者如何阅读。

至少覆盖以下论文类别：

### A. Diffusion 基础论文

* DDPM：Denoising Diffusion Probabilistic Models
* Improved DDPM
* DDIM
* Score-Based Generative Modeling
* EDM / Elucidating the Design Space of Diffusion-Based Generative Models

### B. 条件生成与 guidance

* Classifier Guidance
* Classifier-Free Guidance
* Latent Diffusion Models
* Stable Diffusion 相关基础工作

### C. Diffusion Transformer / 高效架构

* DiT / Diffusion Transformer
* Consistency Models
* Rectified Flow / Flow Matching，如果与文档主题相关

### D. Diffusion for policy / planning

* Diffusion Policy
* Planning with Diffusion
* Diffuser
* Decision Diffuser
* Diffusion-ES
* Gen-Drive
* PlannerRFT

### E. Diffusion in autonomous driving

* Diffusion Planner
* DiffusionDrive
* DiffusionDriveV2，如果有公开信息
* DriveDreamer
* GAIA-1
* Copilot4D
* Vista
* 其他 driving world model / trajectory diffusion / closed-loop planner 相关论文

注意：

* 严禁编造论文结论、实验数据、指标、表格或 ablation。
* 所有论文信息必须注明来源链接。
* 如果无法确认某个结论，必须写“未能确认”。
* 如果当前环境无法联网，请先在文档中标记“需要联网核验”，不要伪造来源。
* 如果当前代码库没有相关实现，不要假装存在；请明确写“当前代码库未发现对应实现”，并给出建议新增位置。

---

## 2.3 Diffusion 在自动驾驶中的应用

请单独建立一个自动驾驶应用章节，重点解释：

1. 为什么自动驾驶规划是多模态生成问题；
2. 普通 trajectory regression 为什么容易平均化；
3. Diffusion planner 如何建模多个合理未来；
4. noisy trajectory `x_t` 是怎么来的；
5. condition `c` 包括什么；
6. score field `∇ log p_t(x_t | c)` 如何理解；
7. trajectory diffusion 的训练 loss；
8. trajectory diffusion 的推理采样流程；
9. guidance / reward / safety constraint 如何作用；
10. DiffusionDrive 为什么要做 truncated diffusion；
11. anchor prior / cascade decoder 的工程意义；
12. diffusion planner 和 world model 的关系；
13. open-loop 与 closed-loop evaluation 的差异；
14. diffusion planner 在真实自动驾驶工程中的风险和限制。

请把以下例子作为重点教学案例：

```text
同一个场景 condition c 下，数据集中有 50 条跟车轨迹 y_F，50 条左转轨迹 y_L。
普通单输出 imitation learning 使用 MSE loss 时，为什么会倾向输出平均轨迹？
Diffusion-based planner 为什么可以学习两个模式？
```

需要从 loss 角度解释：

```text
普通回归：
L(y_hat) = 50 * ||y_hat - y_F||^2 + 50 * ||y_hat - y_L||^2
最优解：
y_hat = (y_F + y_L) / 2
```

然后解释 diffusion-based loss：

```text
x_t = sqrt(alpha_bar_t) * x_0 + sqrt(1 - alpha_bar_t) * epsilon

L = ||epsilon - epsilon_theta(x_t, t, c)||^2
```

并说明：

```text
跟车样本：
x_0 = y_F
x_t 来自 y_F 加噪
模型被训练为把 y_F 附近的 noisy trajectory 去噪回 y_F

左转样本：
x_0 = y_L
x_t 来自 y_L 加噪
模型被训练为把 y_L 附近的 noisy trajectory 去噪回 y_L
```

最后解释 score field：

```text
s_t(x_t | c) = ∇ log p_t(x_t | c)
```

说明它表示：

```text
在给定场景 c 和噪声强度 t 下，当前 noisy trajectory x_t 应该往哪个方向移动，才能更接近真实轨迹分布。
```

并用双峰分布解释：

```text
p_t(x | c) = 0.5 * N(x; μ_F, σ_t^2 I) + 0.5 * N(x; μ_L, σ_t^2 I)

score(x)
= r_F(x) * (μ_F - x) / σ_t^2
+ r_L(x) * (μ_L - x) / σ_t^2
```

其中：

```text
r_F(x) 表示当前位置属于跟车模式的 posterior responsibility；
r_L(x) 表示当前位置属于左转模式的 posterior responsibility。
```

---

# 二、开始前必须先审计代码库

在写文档前，请先完整审计当前 GitHub 代码库结构。

请执行以下步骤：

## Step 1：扫描项目结构

查看：

```bash
find . -maxdepth 3 -type f | sort
```

或者使用等价方法。

识别：

* README；
* docs；
* examples；
* notebooks；
* scripts；
* src；
* tests；
* requirements；
* pyproject / setup；
* 是否已有 MkDocs / Docusaurus / Sphinx / Quarto / Jupyter Book；
* 是否已有 HTML 页面生成方式；
* 是否已有 diffusion 相关代码；
* 是否已有自动驾驶 / trajectory / planning 相关代码。

## Step 2：判断文档落点

根据当前代码库情况选择文档落点。

优先级如下：

1. 如果已有 `docs/`，在 `docs/diffusion/` 下新增；
2. 如果已有 MkDocs，更新 `mkdocs.yml` 导航；
3. 如果已有 Docusaurus，更新 `sidebars.js` 或对应 sidebar 配置；
4. 如果已有 Sphinx，更新 `index.rst`；
5. 如果都没有，则新建：

```text
docs/
└── diffusion/
    ├── index.md
    ├── 01_diffusion_principles.md
    ├── 02_math_to_code.md
    ├── 03_paper_map.md
    ├── 04_diffusion_for_planning.md
    ├── 05_diffusion_autonomous_driving.md
    ├── 06_code_examples.md
    ├── 07_reading_path.md
    └── assets/
```

如果项目更适合 HTML 单页，也可以新建：

```text
docs/diffusion/index.html
```

但要说明为什么选择 HTML 而不是 Markdown。

## Step 3：定位代码实现

搜索以下关键词：

```text
diffusion
ddpm
ddim
noise_scheduler
scheduler
denoise
denoiser
score
unet
transformer
DiT
sample
trajectory
planning
planner
policy
action
waypoint
world_model
```

如果找到相关代码，请记录：

```text
文件路径：
类名 / 函数名：
对应知识点：
对应论文：
当前实现是否完整：
是否有测试：
是否可以作为文档 example：
```

如果没有找到相关代码，请新增 minimal educational examples，不要修改核心业务代码。

---

# 三、建议输出文件结构

请尽量生成如下结构：

```text
docs/diffusion/
├── index.md
├── 01_knowledge_tree.md
├── 02_diffusion_principles.md
├── 03_math_to_code_examples.md
├── 04_paper_map.md
├── 05_paper_deep_dives.md
├── 06_diffusion_for_policy_and_planning.md
├── 07_diffusion_for_autonomous_driving.md
├── 08_toy_implementations.md
├── 09_reading_path.md
├── 10_glossary.md
└── assets/
```

如果适合单文件 HTML，请生成：

```text
docs/diffusion/index.html
```

HTML 页面必须包含：

* 目录导航；
* 知识树；
* 数学公式；
* 代码块；
* paper 卡片；
* paper 对比表；
* 自动驾驶应用章节；
* 可视化图；
* 追问与补充笔记区；
* changelog。

---

# 四、文档内容要求

## 4.1 `index.md`

作为入口页，包含：

* 这个文档体系的目标；
* 推荐阅读路径；
* 知识树总览；
* 关键论文总览；
* 自动驾驶应用总览；
* 如何运行 example；
* 当前版本状态；
* TODO 列表。

## 4.2 `01_knowledge_tree.md`

建立 diffusion 知识树：

```text
Diffusion Models
├── Forward Process
│   ├── Gaussian noising
│   ├── beta schedule
│   ├── alpha / alpha_bar
│   └── q(x_t | x_0)
├── Reverse Process
│   ├── p_theta(x_{t-1} | x_t)
│   ├── denoising network
│   ├── noise prediction
│   └── x_0 prediction
├── Training Objective
│   ├── ELBO
│   ├── simplified loss
│   ├── score matching
│   └── v-prediction
├── Sampling
│   ├── DDPM sampling
│   ├── DDIM sampling
│   ├── fast sampling
│   └── consistency / rectified flow
├── Conditional Generation
│   ├── classifier guidance
│   ├── classifier-free guidance
│   ├── text condition
│   ├── scene condition
│   └── reward guidance
├── Architectures
│   ├── U-Net
│   ├── DiT
│   ├── Transformer denoiser
│   └── latent diffusion
└── Planning Applications
    ├── Diffusion Policy
    ├── Trajectory Diffusion
    ├── Diffusion Planner
    ├── DiffusionDrive
    └── RL / RFT / closed-loop optimization
```

## 4.3 `02_diffusion_principles.md`

讲清 diffusion 原理。

每节必须同时包含：

* 文字解释；
* 数学公式；
* 符号说明；
* 推理过程；
* 代码对应；
* 常见误解。

重点公式包括：

```text
q(x_t | x_0)
x_t = sqrt(alpha_bar_t) * x_0 + sqrt(1 - alpha_bar_t) * epsilon
p_theta(x_{t-1} | x_t)
epsilon_theta(x_t, t, c)
L = ||epsilon - epsilon_theta(x_t, t, c)||^2
score = ∇ log p_t(x_t | c)
```

## 4.4 `03_math_to_code_examples.md`

建立公式到代码的逐行对应。

必须包含 PyTorch 风格 minimal examples：

1. beta schedule；
2. alpha / alpha_bar；
3. add_noise；
4. timestep embedding；
5. simple denoiser；
6. noise prediction loss；
7. DDPM sampling loop；
8. DDIM sampling loop；
9. classifier-free guidance minimal version；
10. trajectory diffusion toy example。

示例代码要求：

* 可以独立阅读；
* 不依赖复杂训练环境；
* 如果不能直接运行，要说明缺失依赖；
* 每个函数有输入输出 shape；
* 对应公式写在代码上方；
* 每行关键代码有解释。

## 4.5 `04_paper_map.md`

整理 paper 地图。

表格字段：

```text
论文
年份
类别
解决的问题
核心方法
对应知识点
是否有代码
是否需要精读
适合阅读顺序
来源链接
```

不要编造来源。

## 4.6 `05_paper_deep_dives.md`

对关键论文做拆解。

每篇论文按以下模板：

```text
# Paper: xxx

## 1. 论文定位
## 2. 它解决的问题
## 3. 前置知识
## 4. 核心方法
## 5. 关键公式
## 6. 算法流程
## 7. 代码对应
## 8. 复现最小 example
## 9. 实验结论
## 10. 局限性
## 11. 和其他论文的关系
## 12. 阅读建议
```

优先精读：

* DDPM
* DDIM
* Classifier-Free Guidance
* Latent Diffusion
* Diffusion Policy
* Diffusion Planner
* DiffusionDrive
* Diffusion-ES
* Gen-Drive
* PlannerRFT

## 4.7 `06_diffusion_for_policy_and_planning.md`

解释 diffusion 在 policy / planning 中的迁移。

重点回答：

* 图像 diffusion 和 action diffusion 的区别；
* 为什么 action sequence 可以被 diffusion 生成；
* policy diffusion 的输入输出；
* trajectory diffusion 的数据结构；
* 为什么多模态 planning 适合 diffusion；
* reward guidance 和 safety constraint 怎么进入。

## 4.8 `07_diffusion_for_autonomous_driving.md`

重点写自动驾驶应用。

必须包含以下章节：

```text
1. 自动驾驶规划为什么是多模态问题
2. 普通 trajectory regression 的平均化问题
3. 从 loss 角度比较 regression 与 diffusion
4. noisy trajectory x_t 是怎么来的
5. condition c 应该包含哪些信息
6. score field 如何理解
7. trajectory diffusion 的训练流程
8. trajectory diffusion 的推理流程
9. guidance / reward / safety constraint
10. Diffusion Planner 论文拆解
11. DiffusionDrive 高效化路线
12. Diffusion-ES / Gen-Drive / PlannerRFT
13. open-loop vs closed-loop evaluation
14. 工程风险与部署限制
```

## 4.9 `08_toy_implementations.md`

写教学代码。

至少包含：

1. 一维双峰分布 diffusion toy example；
2. 二维轨迹 diffusion toy example；
3. 普通 MSE regression 为什么输出均值；
4. diffusion 为什么能生成两个模式；
5. 可视化建议；
6. 如何扩展到自动驾驶 trajectory。

如果当前 repo 支持 notebook，也可以新增：

```text
notebooks/diffusion_toy_bimodal.ipynb
notebooks/trajectory_diffusion_toy.ipynb
```

如果不适合 notebook，则使用 `.py` example：

```text
examples/diffusion/bimodal_regression_vs_diffusion.py
examples/diffusion/trajectory_diffusion_toy.py
```

## 4.10 `09_reading_path.md`

按照学习阶段组织阅读路径：

```text
Stage 1: Diffusion 入门
Stage 2: 数学与代码对应
Stage 3: 条件生成与 guidance
Stage 4: Diffusion Policy
Stage 5: Diffusion Planner
Stage 6: 自动驾驶闭环规划
Stage 7: RL / RFT / Safety Alignment
```

每个阶段给出：

* 目标；
* 必读内容；
* 选读内容；
* 应该能回答的问题；
* 推荐代码练习。

## 4.11 `10_glossary.md`

建立术语表：

```text
x_0
x_t
epsilon
beta_t
alpha_t
alpha_bar_t
denoiser
score
score matching
noise prediction
x_0 prediction
v-prediction
guidance
classifier-free guidance
condition
trajectory diffusion
action diffusion
closed-loop evaluation
reward model
RFT
```

---

# 五、代码与数学对应要求

你必须在文档中明确建立“公式—代码—直觉”的三列对应关系。

示例格式：

| 数学公式                                                        | 代码实现                                     | 直觉解释                    |   |     |                                        |              |
| ----------------------------------------------------------- | ---------------------------------------- | ----------------------- | - | --- | -------------------------------------- | ------------ |
| `x_t = sqrt(alpha_bar_t) x_0 + sqrt(1-alpha_bar_t) epsilon` | `x_t = sqrt_ab * x0 + sqrt_1mab * noise` | 保留一部分真实数据，同时加入一部分高斯噪声   |   |     |                                        |              |
| `L =                                                        |                                          | epsilon - epsilon_theta |   | ^2` | `loss = F.mse_loss(pred_noise, noise)` | 训练模型预测被加入的噪声 |
| `score ≈ -epsilon_theta / sqrt(1-alpha_bar_t)`              | `score = -pred_noise / sqrt_1mab`        | 噪声预测可以转化为去噪方向           |   |     |                                        |              |

---

# 六、自动驾驶章节必须包含的关键解释

请特别详细解释以下内容：

## 6.1 普通回归为什么会平均化

设：

```text
同一个 condition c：
50 条跟车轨迹 y_F
50 条左转轨迹 y_L
```

普通 MSE regression：

```text
L(y_hat) = 50 ||y_hat - y_F||^2 + 50 ||y_hat - y_L||^2
```

最优解：

```text
y_hat = (y_F + y_L) / 2
```

说明：

```text
模型不是不聪明，而是 loss 明确奖励中间解。
```

## 6.2 Diffusion 为什么可以保留两个模式

Diffusion 训练：

```text
x_t = sqrt(alpha_bar_t) x_0 + sqrt(1-alpha_bar_t) epsilon
L = ||epsilon - epsilon_theta(x_t, t, c)||^2
```

对跟车样本：

```text
x_0 = y_F
x_t 来自 y_F 加噪
模型学习把 y_F 附近的 noisy trajectory 去噪回 y_F
```

对左转样本：

```text
x_0 = y_L
x_t 来自 y_L 加噪
模型学习把 y_L 附近的 noisy trajectory 去噪回 y_L
```

所以 diffusion 不是学习：

```text
c -> one average trajectory
```

而是学习：

```text
c + x_t + t -> denoising direction
```

## 6.3 Score field 解释

写清楚：

```text
s_t(x_t | c) = ∇ log p_t(x_t | c)
```

它表示当前 noisy trajectory 应该往哪里移动，使其更接近真实轨迹分布。

对于双峰分布：

```text
p_t(x | c) = 0.5 N(x; μ_F, σ_t^2 I) + 0.5 N(x; μ_L, σ_t^2 I)
```

score：

```text
score(x)
= r_F(x) * (μ_F - x) / σ_t^2
+ r_L(x) * (μ_L - x) / σ_t^2
```

解释：

```text
靠近跟车模式时，r_F 接近 1，score 指向跟车；
靠近左转模式时，r_L 接近 1，score 指向左转；
在中间区域，两个模式共同影响，方向可能不稳定。
```

---

# 七、可视化要求

如果当前文档系统支持图片或 HTML，请新增以下可视化：

1. Diffusion 知识树；
2. Forward process 加噪图；
3. Reverse process 去噪图；
4. MSE regression 平均化图；
5. 双峰分布 score field 图；
6. Trajectory diffusion 训练流程图；
7. Trajectory diffusion 推理流程图；
8. Diffusion Planner 架构图；
9. DiffusionDrive truncated diffusion 图；
10. Open-loop vs closed-loop evaluation 图。

如果使用 HTML，可用 RoughJS / Mermaid / SVG。
如果使用 Markdown，优先使用 Mermaid。
如果不能渲染 Mermaid，请用 ASCII 图，并预留 assets 目录。

---

# 八、质量与可信度要求

请严格遵守：

1. 不要编造论文结论；
2. 不要编造指标；
3. 不要编造 benchmark 结果；
4. 不要把社区观点写成论文结论；
5. 所有论文信息必须给出链接；
6. 对不确定内容标注“未能确认”；
7. 如果代码无法运行，明确说明；
8. 如果当前环境没有 CUDA，不要尝试大型训练；
9. toy code 只用于教学，不声称能复现论文指标；
10. 所有新增文件需要结构清晰、命名统一、可维护。

---

# 九、工作流程

请按以下阶段执行，不要一上来大规模改代码。

## Phase 1：Repo 审计

输出：

```text
1. 当前 repo 结构摘要
2. 已有 docs 系统
3. 已有 diffusion 相关代码
4. 已有 planning / trajectory / autonomous driving 相关代码
5. 推荐文档落点
6. 需要新增的文件列表
```

## Phase 2：文档架构设计

输出：

```text
docs/diffusion/ 的目录结构
每个文件的章节目录
知识树
paper map
代码 example 计划
可视化计划
```

## Phase 3：生成 v0.1 文档

优先生成：

```text
index.md
01_knowledge_tree.md
02_diffusion_principles.md
03_math_to_code_examples.md
07_diffusion_for_autonomous_driving.md
10_glossary.md
```

v0.1 目标是可读、正确、结构完整。

## Phase 4：补充 paper deep dives

生成：

```text
04_paper_map.md
05_paper_deep_dives.md
06_diffusion_for_policy_and_planning.md
09_reading_path.md
```

## Phase 5：补充 toy code / examples

根据 repo 结构新增：

```text
examples/diffusion/bimodal_regression_vs_diffusion.py
examples/diffusion/trajectory_diffusion_toy.py
```

或 notebook。

要求：

* 代码短小；
* 注释清楚；
* 能展示 regression 平均化与 diffusion 多模态采样的差异；
* 不依赖大数据集；
* 不需要 CUDA。

## Phase 6：自检

最后输出：

```text
1. 新增/修改文件列表
2. 每个文件的作用
3. 哪些内容已完成
4. 哪些内容需要人工确认
5. 哪些论文信息需要联网再次核验
6. 如何本地预览文档
7. 如何运行 toy example
8. 后续 v0.2 / v0.3 建议
```

---

# 十、提交要求

请尽量以小步提交方式修改：

1. 先创建文档目录；
2. 再写 index 和 knowledge tree；
3. 再写 principle 和 math-to-code；
4. 再写 autonomous driving application；
5. 再补 paper map；
6. 最后补 examples 和 self-check。

每次修改后请检查：

* Markdown 链接是否有效；
* 文件路径是否正确；
* 代码块语言标注是否正确；
* 数学公式是否能渲染；
* 表格是否完整；
* 是否有重复内容；
* 是否有未核验的断言。

---

# 十一、最终输出格式

请最终回复：

```text
完成情况：
- 新增文件：
- 修改文件：
- 主要内容：
- 关键知识点：
- paper 覆盖：
- toy code：
- 未完成 / 需人工确认：
- 如何预览：
- 如何继续扩展：
```

不要只说“完成了”。必须列出具体文件和内容。
