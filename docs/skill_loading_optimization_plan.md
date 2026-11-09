# 技能列表加载性能优化方案与任务清单

## 一、背景与根因分析

当工作区或全局底座中挂载的 Skill 数量增长后，点击 `/ 技能` 呼出弹窗（`SkillPickerModal`）或输入 `/` 触发自动补全（`CommandAutocomplete`）会出现明显等待（“正在加载技能...”），核心根因包括：

1. **全量下发正文导致的 $O(N)$ 数据与 I/O 膨胀**：列表接口（`/api/v1/skills` 与 `/api/v1/extensions/skills`）为每一条仅展示摘要的列表项全量读取整份 `SKILL.md` 并打包 `sop_content` / `content` / `body`。
2. **前端无缓存、无预热且存在高频重复请求**：
   - `SkillPickerModal` 每次打开均强制清空等待网络返回；
   - `CommandAutocomplete` 在 `useEffect` 依赖中包含 `lastWord`，每输入一个字符就会重新触发全量扫盘请求。
3. **后端同步扫盘阻塞事件循环且无增量缓存**：`SkillService` 与 `SkillLoader` 每次均使用无缓存同步磁盘遍历，直接运行在 FastAPI 异步协程中；且 `GET /api/v1/skills` 前串行查询完整工作区信息（含 `LEFT JOIN conversations GROUP BY` 与 `WORKSPACE.md` 读取）。
4. **前端海量 DOM 一次性渲染**：技能数过多时弹窗一次性挂载全部列表 DOM 节点。

---

## 二、实施方案

### 1. 后端轻量化与高速索引缓存
- **轻 SQL 路径解析**：在 `WorkspaceRepository` 增加 `get_root_path_by_id()`，仅查询 `root_path` 单字段。
- **仅读头部 Frontmatter**：`SkillService.list_all_skills(include_content=False)` 与 `SkillLoader.list_skills(include_content=False)` 默认仅读取 `SKILL.md` 前 4KB 解析 YAML Frontmatter 与摘要元数据，不在列表中传输长篇正文。
- **`os.scandir` + `(st_mtime_ns, st_size)` + 10 秒列表快照三级缓存**：
  - 文件级：基于线程锁维护文件头 `mtime_ns`/`size` 缓存，未修改技能零文件打开直接命中缓存；
  - 列表级：基于根目录 `mtime_ns` 签名 + 10 秒短窗口 TTL + `_CACHE_EPOCH` / `_snapshot_epoch` 主动失效的列表快照缓存，热请求 `<1ms` 零批量 `stat()` 返回；支持 `?refresh=true`（`force_refresh=True`）强制穿透重扫磁盘。
- **按需单条详情懒加载**：优化 `SkillService.get_skill_detail()` 优先 $O(1)$ 直达目标技能目录；在 `/api/v1/extensions/skills/{skill_id}` 新增单条详情查询路由。
- **全链路线程卸载非阻塞**：`/api/v1/skills` 与 `/api/v1/extensions/skills` 均通过 `asyncio.to_thread()` 执行磁盘扫描，不阻塞 FastAPI 事件循环；`context_builder.py` 的技能摘要提示词生成自动复用缓存与轻量化读取。

### 2. 前端 SWR 缓存、后台预热与视口裁剪
- **统一前端缓存层 (`frontend/src/utils/skillCache.ts`)**：按 `workspaceId` 提供内存 + `sessionStorage` 缓存与并发请求去重（In-flight Dedup），支持 `refreshBackend` 参数联动后端 `?refresh=true`。
- **0ms 秒开 (`SkillPickerModal` & `SkillsView`)**：命中缓存立即渲染，仅首次无缓存展示加载状态，后台静默校验更新；增删或点击「扫描同步」主动失效并穿透后端重扫。
- **SOP 正文懒加载 (`SopDrawer` / `CommandAutocomplete` / `MemoryHubModal`)**：抽屉或预览面板打开具体技能时按需拉取单条完整正文，并在本地缓存。
- **修复敲字重复请求 (`CommandAutocomplete`)**：去掉 `useEffect` 中的 `lastWord` 依赖并复用全局扩展缓存，过滤结果截断展示前 60 条。
- **主页静默预热 (`page.tsx`)**：工作区初始化或切换时后台静默预加载当前工作区技能摘要。
- **大列表分批渲染保护 (`SkillPickerModal`)**：默认先渲染前 80 条并提供“展开全部”按钮，搜索时实时过滤。

---

## 三、任务清单

- [x] 完成前后端全链路性能瓶颈勘查与方案设计
- [x] 实现 `WorkspaceRepository.get_root_path_by_id` 与 `SkillService` 轻量头部解析 / 文件与列表级快照缓存 / $O(1)$ 详情查询
- [x] 实现 `SkillLoader` 轻量模式 / 列表快照缓存优化，以及 `skills.py` / `memories.py` 异步化、`?refresh=true` 与单条详情接口
- [x] 更新 `context_builder.py` 复用轻量摘要扫描与 `asyncio.to_thread`
- [x] 实现前端 `skillCache.ts` 与改造 `SkillPickerModal`、`SopDrawer`、`SkillsView`、`CommandAutocomplete`、`MemoryHubModal`、`page.tsx`、`i18nDictionary.ts`
- [x] 完成后端 400/153 技能压测与 FastAPI 路由端到端验证，运行前端 `tsc --noEmit` 与 `next build` 全量构建验证

## 四、实测验证摘要 (`v1.1.4`)

- **工作区技能链路 (`SkillService` · 400 项各 ~30KB)**：
  - 优化前全量返回：`3173.8ms`，JSON 负载 `10600.0 KB`
  - 优化后轻量冷扫：`324.6ms`，JSON 负载 `103.4 KB`（体积缩减 > 99%）
  - 优化后强刷文件缓存：`181.8ms`
  - 优化后热快照命中：`0.22ms`；单条 SOP 按需加载：`1.00ms`
- **全局扩展技能链路 (`SkillLoader` · 153 项)**：
  - 优化前全量返回：`1698.5ms`，JSON 负载 `5984.6 KB`
  - 优化后轻量冷扫：`78.6ms`，JSON 负载 `63.7 KB`（体积缩减 ~98.9%）
  - 优化后强刷文件缓存：`28.5ms`；热快照命中：`0.10ms`
- **构建与静态校验**：Python `py_compile`、FastAPI `TestClient` 路由测试、TypeScript `tsc --noEmit` 与 Next.js `next build`（静态导出 `frontend/out/`）全部通过（退出码 0）。

