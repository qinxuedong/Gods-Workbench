# 拟物硬件按键系统规范

- 状态：CURRENT，2026-10-12按用户追加纠正更新
- 适用：`web/` 原生 HTML、JavaScript、CSS 中实际使用的硬件风格控件
- 项目实现：`web/css/base/hardware-buttons.css`；共享色彩/字阶 token 位于 `web/css/base/hardware-tokens.css`
- 工程权威：仓库 `AGENTS.md` 与 `DESIGN-SYSTEM-GUIDE.md` 中的工程、字体、无障碍和运行约束高于本规范示例

## 1. 设计真源与适配边界

按钮视觉真源是本地原稿：

- `.local/docs/design/按钮设计标准/DESIGN.md`：品牌色、材质与设计元数据。
- `.local/docs/design/按钮设计标准/code.html`：组件结构、交互与样式。
- `.local/docs/design/按钮设计标准/screen.png`：原始总览视觉核对。

`DESIGN.md` 定义品牌元数据；具体按钮的材质、尺寸和交互以 `code.html` 的最终计算样式为准。`custom-refined-overrides` 是原稿最后加载的样式表覆盖层，优先于前面的演示 CSS；内联 `style`、`!important` 与选择器优先级仍须一并核对。`screen.png` 用于总览视觉核对。本文及共享 CSS 是项目适配说明与实现；若与真源冲突，应纠正本文和实现。原稿注释、示例文字或截图与 HTML 实际生效值不同时，以 HTML 的最终计算样式为准。

2026-10-12 用户追加纠正是项目使用范围与交互状态的最新裁决：发光胶囊只用于固定顶栏10个导航按钮；金色主按钮悬浮及按压仍保持金色材质。原稿通用示例不扩大上述使用范围。

项目适配必须同时遵守以下约束：

- 页面主标题/栏目标题维持已确认的 `16px/14px`。原稿有 `48px` 大发射微球样例；输入框行内示例实际使用 `w-8 h-8`，即 `32px`，其“30px”注释不作为尺寸依据。当前实际使用的 AI 发射微球固定 `32px`，不把原稿样例尺寸直接迁移到既有入口。
- 使用项目已准入的 Source Han Sans 本地字体和 `hardware-tokens.css`。不复制原稿中的 Google Fonts、CDN、Tailwind 运行依赖或新建构建链。
- 实现采用项目允许的 `400/500/700` 字重；原稿中 `600/800` 的视觉强调映射到最近的允许字重，不声称对应字重已在产品中加载。
- `:focus-visible`、可访问名称、真实选择/切换 ARIA、`prefers-reduced-motion` 和禁用态是项目工程约束。不得复刻原稿示例中的 `outline:none`，也不得让动画或颜色成为唯一状态信号。
- 原稿里的音频传输、录制、格式选择、示例轮盘等内容只展示组件外观，不授权增加业务动作。只给有实际产品消费者的族系接入产品页面。

## 2. 族系与项目类名

八个视觉族系为 Pill、Tactile、Round Push、Segmented、Rocker、Slide Toggle、Launch Orb、Dial。Segmented 与 Rocker 是原稿组合章节中的两种独立控件；原稿第 08 节是实现速查，不是第八种按键。项目类名是 canonical 原稿类的兼容映射，不改变既有事件、DOM id 或业务状态。

### 2.1 发光长圆胶囊 Pill

- 使用范围：仅每个主页面固定顶栏 `.topbar-master-deck` 内的10个导航按钮。设置侧栏、筛选、剧集切换、画布工具栏、工作流版本及浮窗操作使用常规 `.hw-btn`；选中态由 `.engaged` 与真实 ARIA 状态表达，不添加胶囊发光材质。
- 原稿类：`.btn-pill-active`、`.btn-pill-inactive`、`.btn-pill-disabled`。
- 项目映射：保留上述类，并兼容 `.pill-capsule-active` / `.pill-capsule-inactive`。
- 激活态使用白金到香槟金的径向渐变、白色上沿高光、细腻向下漫反射和暖暗色下沿内阴影；hover 略提亮并增强向下光晕；pressed 下沉 `1.5px`。
- 待命态为暗色金属纵向渐变、弱白色内高光和深色阴影；hover 只提高边框、文字与微光；pressed 下沉 `1px`。
- `.btn-pill-disabled` 保留原稿独立哑光材质；实际禁用行为仍必须由原生 `disabled` 或正确处理的 `aria-disabled="true"` 承载，不能仅靠类名阻止操作。
- Pill 的激活金色径向材质不得套到 Tactile 点火键帽上。

### 2.2 拟物触感键帽 Tactile

- 原稿类：`.btn-tactile-keycap`、`.primary-ignite`、`.btn-tactile-compound`。
- 项目映射：`.btn-hw-tactile`、`.hw-btn`；点火主键 `.hw-btn-gold` / `.primary-ignite`；复合键 `.hw-btn-compound`。
- 普通键帽为 `9px` 倒角的暗色凸起键帽，带顶部白色高光、香槟边缘和底部压影；hover 微亮，pressed 下沉 `1.5px` 并形成内凹阴影。
- `primary-ignite` 是独立金属键帽：使用不随主题语义强调色变化的 `--gw-keycap-gold` / `--gw-keycap-flare`，呈香槟金到青铜纵向渐变、浅金上边、暗金底边；刻字使用 `--gw-engraved-text`，渐变各端对比度不低于 `4.5:1`；默认、hover、pressed 均显式保持金色背景、浅金边框、暗金底边与深色刻字；hover 上浮 `2px` 并提亮 `1.12`，pressed 下沉 `2.5px`、缩至 `.97`、亮度 `.92`，仅以内凹阴影呈现按压，不变黑、不增加外发光。
- 复合键保留自己的整体边框/阴影反馈；`.compound-label` 在 hover 时强调。危险复合键用暗绯红 modifier，不得被金色点火态覆盖。

### 2.3 圆形微动推键 Round Push

- 原稿类：`.btn-hw-round`、`.btn-hw-round-inner`、`.active-power`、`.rec-node-*`。
- 项目映射：`.btn-hw-round` / `.hw-round-btn` 与 `.btn-hw-round-inner` / `.hw-round-inner`。
- 最终覆盖通过 `!important` 固定非禁用状态的外圈边框、阴影和 `transition:none`；非禁用外圈始终为静止 `1.5px` 香槟钛金低透明细环，hover、pressed、通电状态都不得改变外圈边框或阴影。按压行程只发生在内胆。禁用态遵循工程冻结规则，清除光影与动效。内胆宽高由调用方既有尺寸类决定，共享材质规则不得以固定比例覆盖产品尺寸。
- 待命内胆为深色金属态；普通 hover/pressed 只改变内胆。`active-power`、`power-engaged`、现有兼容类 `.active` 及真实 `aria-checked="true"` / `aria-pressed="true"` 对应内胆通电材质：白金至香槟/青铜亮面渐变、浅色内高光及暗金下沿阴影，刻字使用 `--gw-engraved-text`。`.active` 仅映射既有视觉状态，不代替正确的 ARIA 语义。
- `rec-node-standby` / `rec-node-active` 微珠按最终覆盖使用暗绯红；早期紫色 REC 规则已被覆盖，不作为现行视觉。

### 2.4 组合分段选择器 Segmented

- 原稿类：`.btn-segmented-deck`、`.btn-segmented-item`。
- 项目映射：`.btn-segmented-deck` / `.btn-segmented-group` 与 `.btn-segmented-item` / `.hw-segmented-btn`。
- 底座为深色凹槽、`3px` 内边距和克制边线。未选项保持透明暗态；选中项使用暗金属内嵌渐变、香槟金文字、细边及多层克制阴影，不使用整块金色点火材质。
- `.active`、`.is-active`、`aria-selected="true"` 或 `aria-pressed="true"` 必须与真实业务选择状态一致。

### 2.5 双向步进翘板 Rocker

- 原稿类：`.btn-rocker-housing`、`.btn-rocker-paddle`。
- 项目映射：`.btn-rocker-housing` / `.hw-rocker-stepper` 与 `.btn-rocker-paddle` / `.hw-rocker-btn`。
- 与 Segmented 分开实现：保留独立暗色外壳、左右端部异形倒角、机械间隙以及各自的深色凸起桨片。hover 只作轻微金属反光；按压约下沉 `1px` 并转入内凹状态。

### 2.6 滑动拨码 Slide Toggle

- 原稿类：`.btn-slide-toggle`、`.btn-slide-peg`。
- 项目映射：`.btn-slide-toggle` / `.hw-slide-toggle` 与 `.btn-slide-peg` / `.hw-slide-peg`。
- 外壳保留约 `44×22px` 暗色凹轨；拨珠 `16×16px`，具备白金渐变、细白边和金色光晕。激活时拨珠横移 `22px`，增强光效，轨道仍保持暗色。
- 页面必须以按钮或合适的原生切换语义承载操作；若旧消费者仍是非语义容器，不得仅凭外观声明无障碍已完成。

### 2.7 棱彩发射微球 Launch Orb

- 原稿类：`.btn-launch-orb`、`.orb-inner-dot`、`.shockwave-ring`、`.is-firing`。
- 项目映射：`.launch-orb-btn` / `.btn-launch-orb` 与 `.orb-core`；尺寸维持项目裁决 `32×32px`。
- 待命时外圈显示旋转的青、紫、琥珀、洋红棱彩环，外侧带香槟金柔光；中心为暗色内核与青色至紫红、琥珀色的左下至右上渐变脉动点。按压/发射时微球缩至 `.88`，彩环短暂转为香槟金，内点同步转金并缩小。
- 原稿较早的强冲击波声明已被 `custom-refined-overrides` 最终覆盖取代：三道波峰值透明度 `.35`，边线 `.75px`，最大扩散 `scale(1.6)`；错开 `0/.14/.28s` 发射。项目必须保留减少动效和禁用态冻结。
- 微球原稿中的大样例为 `48px`；输入框行内按钮实际是 `w-8 h-8`，即 `32px`，原稿“30px”注释不作为尺寸依据。项目既有入口保持用户确认的 `32px`。原稿所示输入框或 AI 场景不是新增入口授权。

### 2.8 环形扇区轮盘 Dial

- 原稿类：`.radial-dial-group`、`.radial-sector-wedge`、`.active-gold`、`.standby-state`、`.radial-hub-disc`。
- 视觉要点：扇区向心、透底排列；待命为黑曜石金属，选中为香槟金；中心 Hub 弱化。最终覆盖移除扇区滤镜及文字重影，激活文字只留单向浅色微高光。
- 当前 `web/` 无实际产品消费者，故本轮不增加 Dial CSS、模板、JS 或格式选择业务；本节是原稿参考，不代表产品已经实现或验收。

## 3. 项目尺寸与状态映射

| 项目级别 | 高度 | 水平内边距 | 文字 | 图标 | 使用约束 |
| :--- | ---: | ---: | ---: | ---: | :--- |
| LG | `40px` | `20px` | `12px/700` | `16px` | 顶栏与关键主动作；既有导航仍是链接 |
| MD | `32px` | `16px` | `12px/700` | `14px` | 默认硬件按钮 |
| SM | `26px` | `12px` | `11px/700` | `12px` | 稠密行内动作；不缩小正文 |
| XS | `20px` | `8px` | `9.5px` 等宽 | `10px` | 微型状态/预设标签，不承载主要确认 |
| AI Orb | `32×32px` | 不适用 | 不适用 | 内核尺寸由图形确定 | 既有入口的用户确认例外 |

这些数值是项目映射；原稿中的演示 utility、场景和尺寸不直接移植。对小型圆钮另外核验真实触达尺寸及间距，不把 CSS 外接框等同于可用点击目标。

交互状态按组件适用性定义为待命、hover、pressed、engaged/toggle 和 disabled。导航链接不得伪装成开关；瞬时动作不可留在常亮状态；切换外观和 ARIA 必须由同一真实业务状态驱动。统一禁用材质为哑光 `#12141a`、灰字 `#475569`、`opacity:.6`，取消光影、位移、滤镜和动画。

## 4. 无障碍与工程验收

- 所有可操作项使用原生按钮/链接或正确角色；纯图标按钮有可访问名称。Toggle/选择状态按实际语义同步 `aria-checked`、`aria-pressed` 或 `aria-selected`。
- 键盘焦点使用共享 `:focus-visible` 香槟金 `2px` 轮廓及 `2px` offset；不得以移除轮廓替代设计还原。
- 支持 `prefers-reduced-motion`：停用微球旋转/脉动/冲击波和深压位移，保留可理解的瞬时状态反馈。
- 禁用控件不得浮起、发光或响应按压；圆键/拨码/微球的内部材质也必须冻结。
- 项目 CSS 只在共享按钮文件内维护材质实现，并持续对照本地原稿；页面保留业务状态、尺寸 modifier 与布局，不复制整套渐变/阴影。共享 CSS 不替代原始设计真源。
- 设计示例、静态断言、浏览器实际生效值、独立审查、用户视觉验收分别记录。未覆盖的真实业务状态不标完成。

## 5. 本轮状态

本规范已按指定本地原稿纠正；本轮共享样式和前端契约已完成修复并通过 B0a/B1/B2 范围内独立复核。当前只覆盖已迁移族系，不代表八族全部接入、全站 UI 治理完成或用户最终视觉验收通过。全站标题、微球和阶段状态以最新用户故事、Roadmap 及对应本地任务记录为准。
