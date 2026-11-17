---
name: brand-visual-design
command: /brand
version: 2.0.0
category: 视觉与设计
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
description: 品牌视觉与宣发物料设计：涵盖品牌标识、AI Logo、企业视觉识别系统（CIP）、社交媒体横幅海报、品牌语调（Tone of Voice）与合规性规范。
---

# 品牌视觉与宣发物料设计 (Brand & Visual Design)

## 一、核心定位与适用场景
当需要为产品或企业创建/更新品牌标识（Logo）、建立视觉识别系统（CIP）、设计社交媒体横幅（Banner）与宣传海报、或制定对外品牌语调与传播规范时激活此技能。

---

## 二、宣发横幅与海报规范 (Banner Specs)
1. **主流平台尺寸比例**：
   - **网站 Hero 宽屏 Banner**：`1920x1080` (16:9) 或 `1200x630` (社交分享 OpenGraph)
   - **公众号 / 博客文章头图**：`900x383` (2.35:1) 或 `16:9`
   - **X / Twitter 顶栏封面**：`1500x500` (3:1)
   - **应用商店宣发海报**：`1024x500` (横版宣传图)
2. **三段式视觉排版原则**：
   - **视觉视觉锚点 (Visual Anchor)**：核心主视觉/图表置于黄金分割线（左 1/3 或右 1/3）；
   - **核心信息传达 (Core Typography)**：一级大标题不超过 12 个汉字，副标题不超过 2 行；
   - **行动号召与品牌背书 (CTA & Footer)**：按钮与品牌 Logo 置于角落，留足安全呼吸边距。

---

## 三、品牌识别与文案规范 (Brand Voice)
1. **品牌语调 (Tone of Voice)**：
   - 专业严谨、简洁克制、富有远见；
   - 严禁空洞套话与夸大虚假宣传，所有技术优势均以事实与数据作为支撑。
2. **Logo 与色彩保护区**：
   - Logo 周围保留至少等同于自身高度 50% 的不可侵犯安全区；
   - 主色调（Primary）、辅色调（Secondary）与中性底色遵循 60-30-10 黄金色彩平衡法则。

---

## 四、本地生成脚本
- 查询品牌行业风格推荐：
  ```powershell
  python skills/brand-visual-design/scripts/cip/search.py "科技"
  ```
- 提取品牌主色彩与色阶：
  ```powershell
  node skills/brand-visual-design/scripts/extract-colors.cjs
  ```

