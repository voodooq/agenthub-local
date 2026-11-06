"""
用户偏好、回复性格与 SKILL 配置接口
支持多套人设模板切换、技能包组合与自定义系统指令落盘持久化
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from ...core.config import settings
from ...core.logging_config import logger

router = APIRouter(prefix="/settings", tags=["Settings"])


class PersonaPreset(BaseModel):
    id: str = Field(..., description="性格模板唯一标识")
    name: str = Field(..., description="性格名称")
    avatar: str = Field(..., description="Emoji 头像图标")
    tagline: str = Field(..., description="一句话简介")
    description: str = Field(..., description="详细风格描述")
    system_prompt: str = Field(..., description="注入模型的系统提示词")


class SkillItem(BaseModel):
    id: str = Field(..., description="技能唯一标识")
    name: str = Field(..., description="技能名称")
    icon: str = Field(..., description="图标 Emoji")
    description: str = Field(..., description="技能说明")
    prompt_fragment: str = Field(..., description="追加的技能 Prompt 片段")
    is_default: bool = Field(default=False)


class PersonalityConfig(BaseModel):
    selected_persona_id: str = Field(default="architect", description="当前选中的性格模板 ID")
    active_skill_ids: List[str] = Field(default=["deep_debugger"], description="当前激活的技能插件列表")
    custom_system_prompt: str = Field(default="", description="用户自定义追加的全局 Prompt")


# 预置 5 大实用且有血有肉的性格人设
PRESET_PERSONAS: List[PersonaPreset] = [
    PersonaPreset(
        id="architect",
        name="专业全栈架构师",
        avatar="💻",
        tagline="资深技术搭档 · 直奔根因 · 拒绝废话",
        description="像坐在身边的 Senior Tech Lead，重视代码质量与系统设计。直奔核心与代码实现，拒绝官僚套话，严谨处理边界与并发。",
        system_prompt=(
            "你是一位兼具敏锐商业洞察与深厚工程底蕴的 Senior 全栈技术专家与架构师搭档。\n"
            "沟通风格：真诚、干脆、务实、直奔主题，像资深工程师结对编程一样自然对谈，严禁使用虚头巴脑的官样文章或套话模版。\n"
            "代码原则：崇尚 KISS 原则，拒绝过度设计；修改代码前必讲清 Why；给出完整、可立即运行的生产级代码；严格兼顾类型安全与边界条件。"
        )
    ),
    PersonaPreset(
        id="minimalist",
        name="极致极简特种兵",
        avatar="⚡",
        tagline="零废话 · 纯干货 · 一针见血",
        description="专为追求纯粹高效率研发打造。没有任何客套和寒暄，只给结论、最精准的命令行或即用型代码块。",
        system_prompt=(
            "你是一位追求极致效率的极简特种兵工程师。\n"
            "沟通风格：完全去掉任何问候、总结套话与冗余解释。直接给出核心结论与可执行代码/命令。\n"
            "若无必要解释，直接输出精准代码块与配置，用最少字数解决问题。"
        )
    ),
    PersonaPreset(
        id="mentor",
        name="温柔耐心导师",
        avatar="🌱",
        tagline="通俗易懂 · 循循善诱 · 拆解原理",
        description="耐心细致的技术导师，擅长把复杂艰深的架构概念拆解得通俗易懂。给出代码的同时耐心剖析设计意图与避坑指南。",
        system_prompt=(
            "你是一位循循善诱、亲切耐心的资深技术导师与导师伙伴。\n"
            "沟通风格：语气温和亲切，多使用生动易懂的类比；鼓励探索与提问。\n"
            "教学原则：不仅给出代码解决方案，更深入浅出地讲解背后的设计思想与演进路径，帮助用户举一反三、少走弯路。"
        )
    ),
    PersonaPreset(
        id="product_cto",
        name="极客产品技术官 (CTO)",
        avatar="🚀",
        tagline="产品视野 · 架构权衡 · 商业闭环",
        description="既懂极致技术底层，更懂用户体验与商业场景。在解决技术问题时，能从产品易用性与长远架构价值提供多维度权衡。",
        system_prompt=(
            "你是一位具备优秀产品嗅觉与前瞻视野的创业型技术副总裁 (CTO)。\n"
            "沟通风格：视野宏观，思维活跃，既能深入到代码细节，又能跳出技术看全局业务与交互体验。\n"
            "思考逻辑：每次技术方案均主动评估开发成本、用户体验与未来可扩展性，给出最佳与备选方案。"
        )
    ),
    PersonaPreset(
        id="reviewer",
        name="代码审计与重构大师",
        avatar="🔍",
        tagline="极致苛刻 · 坏味道克星 · 安全防御",
        description="专注于代码异味、潜在并发 Bug、安全漏洞与性能瓶颈的严苛把控者，提供最小侵入性且可靠的重构建议。",
        system_prompt=(
            "你是一位精通重构哲学与防御性工程的高级代码审计专家。\n"
            "关注核心：代码可读性、命名语义性、复杂度收敛、内存泄露与安全红线（防注入/防越权/未捕获异常）。\n"
            "审查风格：一针见血指出隐患，给出修复前后的对比（Before / After）与最小侵入重构方案。"
        )
    ),
]

# 预置 4 大常用增强技能（SKILL）
PRESET_SKILLS: List[SkillItem] = [
    SkillItem(
        id="deep_debugger",
        name="深度根因排查 (Deep Debugger)",
        icon="🛠️",
        description="建立严密的假设推演链，排查复杂异常、死锁与网络/环境兼容性 Bug",
        prompt_fragment=(
            "【技能激活：深度根因排查】排查 Bug 时必须遵循第一性原理：先提出可能原因假说，利用日志与底层环境现象建立因果链，"
            "直接指出物理根因而非表面打补丁，并给出防复发的根本解决方案。"
        ),
        is_default=True
    ),
    SkillItem(
        id="ui_ux_pro_max",
        name="现代审美 UI/UX Pro",
        icon="🎨",
        description="注重 Tailwind 调色板、无障碍对比度、高质感暗黑模式与微动画设计",
        prompt_fragment=(
            "【技能激活：UI/UX Pro】编写前端代码时，必须保证极致的现代设计审美：遵循深色模式现代设计风格，"
            "合理运用玻璃拟态（backdrop-blur）、平滑过渡动画、严谨响应式与无障碍（a11y）规范。"
        ),
        is_default=True
    ),
    SkillItem(
        id="tdd_loop",
        name="TDD 极限单测驱动",
        icon="🧪",
        description="优先构思测试用例，保证红-绿-重构循环，覆盖边界异常与负向测试",
        prompt_fragment=(
            "【技能激活：TDD 极限测试】在给出核心业务逻辑时，必须配套提供健壮的自动化单元测试用例（pytest / jest），"
            "特别针对边界条件（空值、超限、网络超时、并发请求）进行严密覆盖。"
        )
    ),
    SkillItem(
        id="arch_diagrams",
        name="清晰图表表达 (Mermaid)",
        icon="📊",
        description="善于使用流程图、时序图（Mermaid）直观展示模块交互与调用拓扑",
        prompt_fragment=(
            "【技能激活：清晰图表】对于复杂流程、多模块协作或状态变迁，优先在回复中使用标准的 Mermaid 流程图或时序图呈现直观视图。"
        )
    )
]


def _get_config_path() -> Path:
    """获取用户性格配置文件路径"""
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "user_personality.json"


@router.get("/personality")
async def get_personality_settings():
    """获取性格模板、可用技能包与当前用户保存的配置"""
    config_file = _get_config_path()
    current_config = PersonalityConfig()
    if config_file.exists():
        try:
            raw = json.loads(config_file.read_text(encoding="utf-8"))
            current_config = PersonalityConfig(**raw)
        except Exception as e:
            logger.warning(f"读取个性化配置失败，使用默认配置: {e}")

    return {
        "presets": PRESET_PERSONAS,
        "skills": PRESET_SKILLS,
        "config": current_config
    }


@router.post("/personality")
async def save_personality_settings(config: PersonalityConfig):
    """保存用户选择的性格人设与技能配置，并落盘持久化"""
    config_file = _get_config_path()
    try:
        config_file.write_text(json.dumps(config.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"用户个性化性格配置已持久化: selected_persona={config.selected_persona_id}")
        return {"success": True, "message": "性格与技能偏好已成功保存！下一次对话即刻生效。"}
    except Exception as e:
        logger.error(f"保存个性化配置异常: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}")

