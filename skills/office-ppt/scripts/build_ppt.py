"""
PPT 演示文稿构建确定性算子 (Office PPT Builder)
基于 python-pptx 实现 16:9 宽屏排版、商务母版配色、要点精简与原生数据图表嵌入。
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# 三套预置商务母版配色
_THEMES: dict[str, dict[str, RGBColor]] = {
    "navy": {
        "primary": RGBColor(15, 41, 66),
        "accent": RGBColor(14, 165, 233),
        "bg": RGBColor(248, 250, 252),
        "card": RGBColor(255, 255, 255),
        "text": RGBColor(30, 41, 59),
        "muted": RGBColor(100, 116, 139),
    },
    "slate": {
        "primary": RGBColor(30, 41, 59),
        "accent": RGBColor(99, 102, 241),
        "bg": RGBColor(249, 250, 251),
        "card": RGBColor(255, 255, 255),
        "text": RGBColor(17, 24, 39),
        "muted": RGBColor(107, 114, 128),
    },
    "emerald": {
        "primary": RGBColor(6, 78, 59),
        "accent": RGBColor(16, 185, 129),
        "bg": RGBColor(246, 251, 249),
        "card": RGBColor(255, 255, 255),
        "text": RGBColor(20, 40, 35),
        "muted": RGBColor(90, 115, 105),
    },
}

_FONT_FAMILY = "微软雅黑"


def _load_params() -> dict[str, Any]:
    """兼容 SKILL_TOOL_ARGS、位置 JSON 参数与 CLI 参数"""
    env_raw = os.environ.get("SKILL_TOOL_ARGS", "").strip()
    if env_raw:
        return json.loads(env_raw)
    if len(sys.argv) >= 2 and sys.argv[1].lstrip().startswith("{"):
        return json.loads(sys.argv[1])

    parser = argparse.ArgumentParser(description="生成 16:9 专业 PPT 演示文稿")
    parser.add_argument("--input-json", required=True, help="PPT 配置 JSON 文件路径")
    parser.add_argument("--output", default="", help="覆盖输出 .pptx 路径")
    args = parser.parse_args()
    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    if args.output:
        data["output_path"] = args.output
    return data


def _set_bg_color(slide: Any, color: RGBColor) -> None:
    """设置单页纯色背景"""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_header_bar(slide: Any, title: str, theme: dict[str, RGBColor], page_num: int) -> None:
    """添加统一内页顶部标题栏与页脚页码"""
    _set_bg_color(slide, theme["bg"])

    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.12))
    bar.fill.solid()
    bar.fill.fore_color.rgb = theme["accent"]
    bar.line.fill.background()

    title_box = slide.shapes.add_textbox(Inches(0.7), Inches(0.32), Inches(11.8), Inches(0.75))
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.name = _FONT_FAMILY
    p.font.size = Pt(26)
    p.font.bold = True
    p.font.color.rgb = theme["primary"]

    footer = slide.shapes.add_textbox(Inches(11.6), Inches(7.05), Inches(1.2), Inches(0.3))
    fp = footer.text_frame.paragraphs[0]
    fp.text = f"{page_num}"
    fp.alignment = PP_ALIGN.RIGHT
    fp.font.name = _FONT_FAMILY
    fp.font.size = Pt(11)
    fp.font.color.rgb = theme["muted"]


def _render_bullets(
    slide: Any,
    bullets: list[str],
    left: float,
    top: float,
    width: float,
    height: float,
    theme: dict[str, RGBColor],
    font_size: int = 18,
) -> None:
    """渲染精简要点列表（单页最多保留 6 条以保证可读性）"""
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    card.fill.solid()
    card.fill.fore_color.rgb = theme["card"]
    card.line.color.rgb = RGBColor(226, 232, 240)

    box = slide.shapes.add_textbox(Inches(left + 0.35), Inches(top + 0.25), Inches(width - 0.7), Inches(height - 0.5))
    tf = box.text_frame
    tf.word_wrap = True

    trimmed = [str(b).strip() for b in bullets if str(b).strip()][:6]
    for idx, text in enumerate(trimmed):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.text = f"•  {text}"
        p.space_after = Pt(14)
        p.font.name = _FONT_FAMILY
        p.font.size = Pt(font_size)
        p.font.color.rgb = theme["text"]


def _add_cover_slide(prs: Presentation, spec: dict[str, Any], theme: dict[str, RGBColor]) -> None:
    """构建封面页或结束页"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg_color(slide, theme["primary"])

    accent_strip = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(2.2), Inches(0.14), Inches(2.6))
    accent_strip.fill.solid()
    accent_strip.fill.fore_color.rgb = theme["accent"]
    accent_strip.line.fill.background()

    title_box = slide.shapes.add_textbox(Inches(1.2), Inches(2.2), Inches(11.0), Inches(1.6))
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = str(spec.get("title") or "演示汇报")
    p.font.name = _FONT_FAMILY
    p.font.size = Pt(40)
    p.font.bold = True
    p.font.color.rgb = RGBColor(255, 255, 255)

    subtitle = str(spec.get("subtitle") or "").strip()
    if subtitle:
        sub_box = slide.shapes.add_textbox(Inches(1.2), Inches(3.85), Inches(10.8), Inches(0.8))
        stf = sub_box.text_frame
        stf.word_wrap = True
        sp = stf.paragraphs[0]
        sp.text = subtitle
        sp.font.name = _FONT_FAMILY
        sp.font.size = Pt(20)
        sp.font.color.rgb = RGBColor(203, 213, 225)

    meta = str(spec.get("meta") or "").strip()
    if meta:
        meta_box = slide.shapes.add_textbox(Inches(1.2), Inches(6.2), Inches(10.0), Inches(0.5))
        mp = meta_box.text_frame.paragraphs[0]
        mp.text = meta
        mp.font.name = _FONT_FAMILY
        mp.font.size = Pt(13)
        mp.font.color.rgb = RGBColor(148, 163, 184)


def _add_chart_slide(prs: Presentation, spec: dict[str, Any], theme: dict[str, RGBColor], page_num: int) -> None:
    """构建原生数据图表页（柱状图/折线图/饼图）"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_header_bar(slide, str(spec.get("title") or "数据分析"), theme, page_num)

    chart_data = CategoryChartData()
    categories = [str(c) for c in (spec.get("categories") or [])]
    chart_data.categories = categories
    for s in spec.get("series") or []:
        chart_data.add_series(str(s.get("name") or "系列"), tuple(float(v) for v in (s.get("values") or [])))

    chart_kind = str(spec.get("chart_type") or "bar").lower()
    xl_type = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }.get(chart_kind, XL_CHART_TYPE.COLUMN_CLUSTERED)

    takeaway = str(spec.get("takeaway") or "").strip()
    chart_height = Inches(5.0) if takeaway else Inches(5.6)

    chart_frame = slide.shapes.add_chart(
        xl_type,
        Inches(0.8),
        Inches(1.25),
        Inches(11.7),
        chart_height,
        chart_data,
    )
    chart = chart_frame.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.TOP
    chart.legend.include_in_layout = False

    if takeaway:
        banner = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(6.35), Inches(11.7), Inches(0.58))
        banner.fill.solid()
        banner.fill.fore_color.rgb = RGBColor(224, 242, 254)
        banner.line.color.rgb = theme["accent"]
        btf = banner.text_frame
        btf.word_wrap = True
        bp = btf.paragraphs[0]
        bp.text = f"核心洞察：{takeaway}"
        bp.font.name = _FONT_FAMILY
        bp.font.size = Pt(14)
        bp.font.bold = True
        bp.font.color.rgb = theme["primary"]


def build_ppt(params: dict[str, Any]) -> dict[str, Any]:
    """根据结构化页面定义生成 16:9 PPTX 文件"""
    theme_name = str(params.get("theme") or "navy").lower()
    theme = _THEMES.get(theme_name, _THEMES["navy"])

    prs = Presentation()
    # 16:9 宽屏尺寸
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    slides_spec = params.get("slides") or []
    if not slides_spec:
        raise ValueError("slides 不能为空")

    for idx, spec in enumerate(slides_spec, start=1):
        layout = str(spec.get("layout") or "bullets").lower()
        if layout in ("cover", "closing"):
            _add_cover_slide(prs, spec, theme)
        elif layout == "chart":
            _add_chart_slide(prs, spec, theme, idx)
        elif layout == "two_column":
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            _add_header_bar(slide, str(spec.get("title") or ""), theme, idx)
            left_title = str(spec.get("left_title") or "维度一")
            right_title = str(spec.get("right_title") or "维度二")
            lt_box = slide.shapes.add_textbox(Inches(0.7), Inches(1.2), Inches(5.7), Inches(0.4))
            ltp = lt_box.text_frame.paragraphs[0]
            ltp.text = left_title
            ltp.font.name = _FONT_FAMILY
            ltp.font.size = Pt(18)
            ltp.font.bold = True
            ltp.font.color.rgb = theme["accent"]

            rt_box = slide.shapes.add_textbox(Inches(6.9), Inches(1.2), Inches(5.7), Inches(0.4))
            rtp = rt_box.text_frame.paragraphs[0]
            rtp.text = right_title
            rtp.font.name = _FONT_FAMILY
            rtp.font.size = Pt(18)
            rtp.font.bold = True
            rtp.font.color.rgb = theme["accent"]

            _render_bullets(slide, spec.get("left_bullets") or [], 0.7, 1.65, 5.7, 5.1, theme, font_size=16)
            _render_bullets(slide, spec.get("right_bullets") or [], 6.9, 1.65, 5.7, 5.1, theme, font_size=16)
        else:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            _add_header_bar(slide, str(spec.get("title") or ""), theme, idx)
            _render_bullets(slide, spec.get("bullets") or [], 0.7, 1.3, 11.9, 5.5, theme, font_size=19)

    raw_out = str(params.get("output_path") or "output/slides/presentation.pptx").strip()
    out_path = Path(raw_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))

    return {
        "status": "success",
        "output_path": str(out_path),
        "slide_count": len(prs.slides),
        "theme": theme_name,
    }


def main() -> None:
    """算子主入口"""
    params = _load_params()
    result = build_ppt(params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
